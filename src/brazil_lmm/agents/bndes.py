"""
BNDES Transparência agent.

BNDES publishes all its credit operations at:
  https://www.bndes.gov.br/wps/portal/site/home/transparencia/consulta-operacoes-bndes

The data is available as downloadable CSVs (updated monthly). This agent:
1. Downloads the current operational CSV from BNDES open data.
2. Filters by CNPJ.
3. Returns all matching BNDESContract records.

BNDES open data endpoint (JSON query via their API):
  https://operacoes.bndes.gov.br/api/operacoes?cnpj={cnpj}

We use their REST API when available, CSV bulk download as fallback.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date

import httpx

from brazil_lmm.agents.base import BaseAgent
from brazil_lmm.models import BNDESContract, PartialCompany


# BNDES public REST API (undocumented but stable)
BNDES_API_URL = "https://operacoes.bndes.gov.br/api/operacoes"

# Bulk CSV — updated monthly by BNDES
BNDES_CSV_URL = (
    "https://www.bndes.gov.br/arquivos/transparencia/"
    "planilhas-operacoes-contratadas/operacoes-contratadas.csv"
)


class BNDESAgent(BaseAgent):
    name = "bndes"

    async def enrich(self, cnpj: str, company_name: str | None = None) -> PartialCompany:
        contracts = await self._fetch_via_api(cnpj)
        if not contracts:
            contracts = await self._fetch_via_csv(cnpj)

        return PartialCompany(
            cnpj=cnpj,
            bndes_contracts=contracts,
            source=self.name,
            confidence=0.95 if contracts else 0.5,
        )

    async def _fetch_via_api(self, cnpj: str) -> list[BNDESContract]:
        try:
            resp = await self._get(
                BNDES_API_URL,
                params={"cnpjBeneficiarioFinal": cnpj, "size": 100},
            )
            data = resp.json()
            items = data if isinstance(data, list) else data.get("content", [])
            return [self._parse_api_item(item) for item in items if item]
        except (httpx.HTTPStatusError, httpx.RequestError, KeyError):
            return []

    def _parse_api_item(self, item: dict) -> BNDESContract:
        return BNDESContract(
            contract_number=str(item.get("numeroContrato", "")),
            product=item.get("produto", ""),
            agent_bank=item.get("agente", None),
            value_brl=float(item.get("valorContratado", 0) or 0),
            approval_date=self._parse_date(item.get("dataContratacao", "")),
            status=item.get("situacao", None),
            sector_bndes=item.get("setorCnae", None),
            municipality=item.get("municipio", None),
            uf=item.get("uf", None),
        )

    async def _fetch_via_csv(self, cnpj: str) -> list[BNDESContract]:
        """
        Fallback: download the full CSV and filter locally.
        The CSV is ~50MB; we stream it to avoid loading everything into RAM.
        """
        contracts: list[BNDESContract] = []
        try:
            async with self.client.stream("GET", BNDES_CSV_URL) as resp:
                resp.raise_for_status()
                raw_bytes = await resp.aread()

            # BNDES CSV is latin-1 encoded
            content = raw_bytes.decode("latin-1")
            reader = csv.DictReader(io.StringIO(content), delimiter=";")

            for row in reader:
                row_cnpj = re.sub(r"\D", "", row.get("CNPJ do Beneficiário Final", ""))
                if row_cnpj != cnpj:
                    continue
                contracts.append(self._parse_csv_row(row))

        except (httpx.HTTPStatusError, httpx.RequestError, Exception):
            pass

        return contracts

    def _parse_csv_row(self, row: dict) -> BNDESContract:
        value_str = row.get("Valor Contratado (R$)", "0").replace(".", "").replace(",", ".")
        try:
            value = float(value_str)
        except ValueError:
            value = 0.0

        return BNDESContract(
            contract_number=row.get("Número do Contrato", ""),
            product=row.get("Produto", ""),
            agent_bank=row.get("Agente Financeiro", None),
            value_brl=value,
            approval_date=self._parse_date(row.get("Data de Contratação", "")),
            status=row.get("Situação", None),
            sector_bndes=row.get("Setor CNAE", None),
            municipality=row.get("Município do Beneficiário Final", None),
            uf=row.get("UF", None),
        )

    @staticmethod
    def _parse_date(raw: str) -> date | None:
        if not raw:
            return None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                from datetime import datetime
                return datetime.strptime(raw.strip(), fmt).date()
            except ValueError:
                continue
        return None
