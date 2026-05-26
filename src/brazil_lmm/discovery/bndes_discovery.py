"""
BNDES Discovery — finds candidate companies from BNDES Transparência CSV.

Strategy:
- Download the public BNDES operations CSV (all contracts ever approved)
- Filter by sector (CNAE prefix) and UF
- Use contract value as a rough size proxy
  (companies borrowing R$1M–R$100M from BNDES are typically LMM)
- Deduplicate by CNPJ and return ranked candidate list

This is the richest free discovery source because:
1. It's official and complete
2. Already pre-filtered to companies that USE credit — perfect for a financing offer
3. Has sector, UF, contract value, and date
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

import httpx

from brazil_lmm.discovery.sector_map import INDUSTRY_CNAES, HEALTH_CNAES


BNDES_CSV_URL = (
    "https://www.bndes.gov.br/arquivos/transparencia/"
    "planilhas-operacoes-contratadas/operacoes-contratadas.csv"
)

# Contract value range as size proxy for LMM (R$50M–R$850M revenue)
# Companies in that revenue range typically borrow R$500K–R$80M from BNDES
MIN_CONTRACT_BRL = 500_000
MAX_CONTRACT_BRL = 80_000_000


@dataclass
class DiscoveredCompany:
    cnpj: str
    razao_social: str
    sector_hint: str
    uf: str
    city: str
    total_bndes_brl: float
    contract_count: int
    latest_year: int | None
    discovery_source: str = "bndes"
    score_hint: float = 0.0  # pre-enrichment signal


class BNDESDiscovery:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=120.0, follow_redirects=True)

    async def discover(
        self,
        sectors: list[str],          # ["industria", "saude"]
        ufs: list[str] | None = None, # ["SP","RJ"] or None = all
        limit: int = 500,
    ) -> list[DiscoveredCompany]:
        allowed_cnaes = self._sector_cnaes(sectors)
        raw = await self._download_csv()
        companies = self._parse(raw, allowed_cnaes, ufs)
        companies = self._rank(companies)
        return companies[:limit]

    async def _download_csv(self) -> str:
        resp = await self._client.get(BNDES_CSV_URL)
        resp.raise_for_status()
        return resp.content.decode("latin-1")

    def _parse(
        self,
        content: str,
        allowed_cnaes: set[str],
        ufs: list[str] | None,
    ) -> list[DiscoveredCompany]:
        reader = csv.DictReader(io.StringIO(content.replace('\r\n', '\n').replace('\r', '\n')), delimiter=";", quoting=csv.QUOTE_NONE, escapechar='\\')
        aggregated: dict[str, DiscoveredCompany] = {}

        for row in reader:
            cnpj = re.sub(r"\D", "", row.get("CNPJ do Beneficiário Final", ""))
            if len(cnpj) != 14:
                continue

            uf = (row.get("UF", "") or "").strip().upper()
            if ufs and uf not in [u.upper() for u in ufs]:
                continue

            cnae_raw = (row.get("CNAE", "") or row.get("Setor CNAE", "") or "").strip()
            cnae_prefix = re.sub(r"\D", "", cnae_raw)[:2]
            if allowed_cnaes and cnae_prefix not in allowed_cnaes:
                continue

            value_str = (row.get("Valor Contratado (R$)", "") or "0").replace(".", "").replace(",", ".")
            try:
                value = float(value_str)
            except ValueError:
                value = 0.0

            if value < MIN_CONTRACT_BRL or value > MAX_CONTRACT_BRL:
                continue

            razao = (row.get("Beneficiário Final", "") or row.get("Razão Social", "") or "").strip()
            city = (row.get("Município do Beneficiário Final", "") or "").strip().title()
            sector_hint = cnae_raw

            date_str = row.get("Data de Contratação", "") or ""
            year_match = re.search(r"\d{4}", date_str)
            year = int(year_match.group()) if year_match else None

            if cnpj in aggregated:
                existing = aggregated[cnpj]
                existing.total_bndes_brl += value
                existing.contract_count += 1
                if year and (existing.latest_year is None or year > existing.latest_year):
                    existing.latest_year = year
            else:
                aggregated[cnpj] = DiscoveredCompany(
                    cnpj=cnpj,
                    razao_social=razao,
                    sector_hint=sector_hint,
                    uf=uf,
                    city=city,
                    total_bndes_brl=value,
                    contract_count=1,
                    latest_year=year,
                )

        return list(aggregated.values())

    def _rank(self, companies: list[DiscoveredCompany]) -> list[DiscoveredCompany]:
        """
        Pre-enrichment rank signal:
        - More contracts = more credit-mature
        - Recent contracts = actively growing
        - Higher total = larger company
        """
        for c in companies:
            score = 0.0
            score += min(c.contract_count / 10, 0.3)          # up to 0.3 for repeat borrowing
            score += min(c.total_bndes_brl / 80_000_000, 0.4)  # up to 0.4 for size
            score += 0.3 if (c.latest_year and c.latest_year >= 2020) else 0.0
            c.score_hint = round(score, 3)

        return sorted(companies, key=lambda x: x.score_hint, reverse=True)

    @staticmethod
    def _sector_cnaes(sectors: list[str]) -> set[str]:
        result: set[str] = set()
        for s in sectors:
            if "industria" in s.lower() or "indústria" in s.lower():
                result |= INDUSTRY_CNAES
            if "saude" in s.lower() or "saúde" in s.lower():
                result |= HEALTH_CNAES
        return result

    async def close(self) -> None:
        await self._client.aclose()
