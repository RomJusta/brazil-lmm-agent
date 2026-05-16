"""
FINEP enrichment agent.

FINEP (Financiadora de Estudos e Projetos) publishes beneficiaries through:
1. FINEP Consulta Pública portal (web-scraped)
2. Diário Oficial da União (DOU) via LexML XML API — most reliable bulk source
3. Portal da Transparência Federal (beneficiaries of subvenções)

Strategy:
- Primary: Portal da Transparência Federal /api-de-dados/contratos endpoint filtered by CNPJ.
- Secondary: LexML DOU search for the CNPJ string in official acts.
- Modality inference from program name keywords.
"""
from __future__ import annotations

import re
from typing import Literal

import httpx

from brazil_lmm.agents.base import BaseAgent
from brazil_lmm.models import FINEPContract, PartialCompany


TRANSPARENCIA_URL = "https://api.portaldatransparencia.gov.br/api-de-dados/contratos"
LEXML_URL = "https://www.lexml.gov.br/busca/SRU"


MODALITY_KEYWORDS: dict[str, Literal["subvencao", "credito_reembolsavel", "credito_nao_reembolsavel"]] = {
    "subvenção": "subvencao",
    "subvencao": "subvencao",
    "encomenda": "subvencao",
    "juro zero": "credito_nao_reembolsavel",
    "não reembolsável": "credito_nao_reembolsavel",
    "nao reembolsavel": "credito_nao_reembolsavel",
    "reembolsável": "credito_reembolsavel",
    "reembolsavel": "credito_reembolsavel",
    "crédito": "credito_reembolsavel",
}

# FINEP CNPJ (to filter Portal da Transparência as contracting party)
FINEP_CNPJ = "33749086000109"


class FINEPAgent(BaseAgent):
    name = "finep"

    def __init__(self, transparencia_api_key: str | None = None) -> None:
        super().__init__()
        self._api_key = transparencia_api_key

    async def enrich(self, cnpj: str, company_name: str | None = None) -> PartialCompany:
        contracts: list[FINEPContract] = []

        contracts += await self._fetch_transparencia(cnpj)
        if not contracts:
            contracts += await self._fetch_lexml(cnpj, company_name)

        return PartialCompany(
            cnpj=cnpj,
            finep_contracts=contracts,
            source=self.name,
            confidence=0.85 if contracts else 0.4,
        )

    async def _fetch_transparencia(self, cnpj: str) -> list[FINEPContract]:
        """
        Portal da Transparência lists contracts where FINEP is the grantor
        and the CNPJ is the beneficiary.
        Requires an API key (free registration at portaldatransparencia.gov.br).
        """
        if not self._api_key:
            return []

        headers = {"chave-api-dados": self._api_key}
        contracts: list[FINEPContract] = []

        try:
            resp = await self._get(
                TRANSPARENCIA_URL,
                params={
                    "cnpjContratado": cnpj,
                    "orgaoSuperior": "24000",  # MCTI — FINEP's ministry
                    "pagina": 1,
                },
                headers=headers,
            )
            items = resp.json()
            if not isinstance(items, list):
                return []

            for item in items:
                # Filter to FINEP-originated contracts only
                org = item.get("unidadeGestora", {}).get("orgao", {})
                cnpj_org = re.sub(r"\D", "", org.get("cnpj", ""))
                if cnpj_org != FINEP_CNPJ:
                    continue
                contracts.append(self._parse_transparencia_item(item))

        except (httpx.HTTPStatusError, httpx.RequestError, KeyError, ValueError):
            pass

        return contracts

    def _parse_transparencia_item(self, item: dict) -> FINEPContract:
        program = (
            item.get("objeto", "")
            or item.get("modalidadeCompra", {}).get("descricao", "")
            or "FINEP"
        )
        value_str = str(item.get("valorInicial", 0) or 0)
        try:
            value = float(value_str.replace(",", "."))
        except ValueError:
            value = 0.0

        year_raw = item.get("dataAssinatura", "")
        year: int | None = None
        if year_raw:
            m = re.search(r"\d{4}", year_raw)
            year = int(m.group()) if m else None

        return FINEPContract(
            program=program[:200],
            value_brl=value,
            year=year,
            modality=self._infer_modality(program),
            dou_reference=item.get("numeroProcesso", None),
        )

    async def _fetch_lexml(self, cnpj: str, company_name: str | None) -> list[FINEPContract]:
        """
        Search the DOU via LexML SRU for mentions of this CNPJ alongside FINEP.
        Returns partial records — value is often not in the DOU text.
        """
        query_terms = [f'"{cnpj}"', "FINEP"]
        if company_name:
            query_terms.append(f'"{company_name}"')

        query = " AND ".join(query_terms)
        contracts: list[FINEPContract] = []

        try:
            resp = await self._get(
                LEXML_URL,
                params={
                    "operation": "searchRetrieve",
                    "version": "1.1",
                    "query": query,
                    "maximumRecords": 10,
                    "recordSchema": "marcxml",
                },
            )
            # Parse XML — extract titles and dates
            text = resp.text
            titles = re.findall(r"<dc:title[^>]*>([^<]+)</dc:title>", text)
            dates = re.findall(r"<dc:date[^>]*>(\d{4})", text)
            identifiers = re.findall(r"<dc:identifier[^>]*>([^<]+)</dc:identifier>", text)

            for i, title in enumerate(titles):
                year = int(dates[i]) if i < len(dates) else None
                dou_ref = identifiers[i] if i < len(identifiers) else None
                contracts.append(FINEPContract(
                    program=title[:200],
                    year=year,
                    modality=self._infer_modality(title),
                    dou_reference=dou_ref,
                    description=title,
                ))

        except (httpx.HTTPStatusError, httpx.RequestError, Exception):
            pass

        return contracts

    @staticmethod
    def _infer_modality(
        text: str,
    ) -> Literal["subvencao", "credito_reembolsavel", "credito_nao_reembolsavel", "unknown"]:
        lower = text.lower()
        for keyword, modality in MODALITY_KEYWORDS.items():
            if keyword in lower:
                return modality
        return "unknown"
