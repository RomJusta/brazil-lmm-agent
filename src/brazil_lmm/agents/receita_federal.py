"""
Receita Federal enrichment agent.

Uses the BrasilAPI (brasilapi.com.br) as a free, reliable proxy to the
Receita Federal CNPJ database. Falls back to receitaws.com.br if needed.
Returns: razao_social, nome_fantasia, CNAE, address, founding date, QSA (owners).
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from brazil_lmm.agents.base import BaseAgent
from brazil_lmm.models import Owner, PartialCompany, Person


BRASILAPI_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
RECEITAWS_URL = "https://www.receitaws.com.br/v1/cnpj/{cnpj}"

# CNAE codes that typically indicate a tech/innovation company
TECH_CNAES = {
    "6201", "6202", "6203", "6204", "6209",  # Software/IT services
    "6311", "6319", "6399",                   # Data services
    "7210", "7220",                            # R&D
}


class ReceitaFederalAgent(BaseAgent):
    name = "receita_federal"

    async def enrich(self, cnpj: str, company_name: str | None = None) -> PartialCompany:
        data = await self._fetch_cnpj(cnpj)
        if not data:
            return PartialCompany(cnpj=cnpj, source=self.name, confidence=0.0)

        cnae_raw = self._extract_cnae(data)
        owners, ceo = self._extract_people(data, cnpj)
        founded_year = self._extract_year(data.get("data_inicio_atividade", ""))

        return PartialCompany(
            cnpj=cnpj,
            razao_social=data.get("razao_social", ""),
            nome_fantasia=data.get("nome_fantasia") or None,
            cnae_primary=cnae_raw,
            sector=None,  # orchestrator derives this from CNAE
            founded_year=founded_year,
            website=None,  # Receita Federal doesn't expose website
            address_city=data.get("municipio", "").title() or None,
            address_uf=data.get("uf") or None,
            is_active=self._is_active(data),
            owners=owners,
            ceo=ceo,
            source=self.name,
            confidence=0.9,
        )

    async def _fetch_cnpj(self, cnpj: str) -> dict | None:
        for url_template in [BRASILAPI_URL, RECEITAWS_URL]:
            try:
                resp = await self._get(url_template.format(cnpj=cnpj))
                return resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError):
                continue
        return None

    def _extract_cnae(self, data: dict) -> str | None:
        # BrasilAPI returns cnae_fiscal or cnae_fiscal_descricao
        code = data.get("cnae_fiscal") or data.get("cnae_fiscal_codigo")
        if code:
            return str(code)
        # receitaws format
        atividade = data.get("atividade_principal", [{}])
        if atividade:
            raw = atividade[0].get("code", "")
            return raw if raw else None
        return None

    def _extract_people(self, data: dict, cnpj: str) -> tuple[list[Owner], Person | None]:
        owners: list[Owner] = []
        ceo: Person | None = None

        # BrasilAPI QSA field
        qsa = data.get("qsa", [])
        for member in qsa:
            name = member.get("nome_socio", "") or member.get("nome", "")
            qual = member.get("qualificacao_socio", "") or member.get("qual", "")
            cnpj_cpf = member.get("cnpj_cpf_do_socio", "")

            entity_type: str = "PF"
            masked: str | None = None
            if cnpj_cpf:
                digits = re.sub(r"\D", "", cnpj_cpf)
                if len(digits) == 14:
                    entity_type = "PJ"
                    masked = digits  # full CNPJ is public
                else:
                    entity_type = "PF"
                    masked = f"***{digits[-3:]}" if len(digits) >= 3 else None

            owner = Owner(
                name=name,
                entity_type=entity_type,  # type: ignore[arg-type]
                cnpj_or_cpf_masked=masked,
                source="receita_federal_qsa",
            )
            owners.append(owner)

            # Heuristic: qualifications 5, 16, 49, 65 are CEO/director roles
            ceo_qualifications = {"05", "16", "49", "65", "10", "22"}
            qual_code = re.sub(r"\D", "", qual)[:2]
            if qual_code in ceo_qualifications and ceo is None:
                ceo = Person(
                    full_name=name,
                    role=qual if qual else "Sócio-Administrador",
                    source="receita_federal_qsa",
                )

        return owners, ceo

    def _extract_year(self, raw: str) -> int | None:
        # Formats: "2010-03-15" or "15/03/2010"
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(raw, fmt).year
            except ValueError:
                continue
        return None

    def _is_active(self, data: dict) -> bool:
        raw = data.get("situacao_cadastral") or data.get("situacao") or ""
        status = str(raw).upper().strip()
        return "ATIVA" in status or status == "2"
