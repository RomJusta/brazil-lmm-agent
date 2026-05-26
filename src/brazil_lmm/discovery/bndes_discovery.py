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

import re
from dataclasses import dataclass, field

import httpx

from brazil_lmm.discovery.sector_map import INDUSTRY_CNAES, HEALTH_CNAES


# BNDES REST API — paginated, filterable by sector/UF
BNDES_API_URL = "https://operacoes.bndes.gov.br/api/operacoes"

# Contract value range as size proxy for LMM (R$50M–R$850M revenue)
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
        sectors: list[str],
        ufs: list[str] | None = None,
        limit: int = 500,
    ) -> list[DiscoveredCompany]:
        allowed_cnaes = self._sector_cnaes(sectors)
        aggregated: dict[str, DiscoveredCompany] = {}
        page = 1
        page_size = 100

        while len(aggregated) < limit:
            params: dict = {"size": page_size, "pagina": page}
            if ufs:
                params["uf"] = ufs[0]  # API takes one UF at a time

            try:
                resp = await self._client.get(BNDES_API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[BNDES] API error page {page}: {e}")
                break

            items = data if isinstance(data, list) else data.get("content", [])
            if not items:
                break

            print(f"[BNDES] Page {page}: {len(items)} items, sample keys: {list(items[0].keys())[:8] if items else []}")

            for item in items:
                cnpj = re.sub(r"\D", "", str(item.get("cnpjBeneficiarioFinal", "") or ""))
                if len(cnpj) != 14:
                    continue

                uf = (item.get("uf", "") or "").strip().upper()
                if ufs and uf not in [u.upper() for u in ufs]:
                    continue

                cnae_raw = str(item.get("setorCnae", "") or item.get("setor", "") or "")
                cnae_prefix = re.sub(r"\D", "", cnae_raw)[:2]
                # Temporarily skip CNAE filter to confirm API works
                # if allowed_cnaes and cnae_prefix not in allowed_cnaes:
                #     continue

                try:
                    value = float(item.get("valorContratado", 0) or 0)
                except (ValueError, TypeError):
                    value = 0.0

                # Temporarily permissive — log value to tune range
                print(f"[BNDES] value={value} cnae={cnae_prefix} uf={uf} cnpj={cnpj[:6]}...")
                if value > 0 and value > MAX_CONTRACT_BRL * 10:
                    continue  # only skip truly huge contracts

                razao = str(item.get("nomeCliente", "") or item.get("beneficiarioFinal", "") or "")
                city = str(item.get("municipio", "") or "").title()
                date_str = str(item.get("dataContratacao", "") or "")
                year_match = re.search(r"\d{4}", date_str)
                year = int(year_match.group()) if year_match else None

                if cnpj in aggregated:
                    aggregated[cnpj].total_bndes_brl += value
                    aggregated[cnpj].contract_count += 1
                    if year and (aggregated[cnpj].latest_year is None or year > aggregated[cnpj].latest_year):
                        aggregated[cnpj].latest_year = year
                else:
                    aggregated[cnpj] = DiscoveredCompany(
                        cnpj=cnpj,
                        razao_social=razao,
                        sector_hint=cnae_raw,
                        uf=uf,
                        city=city,
                        total_bndes_brl=value,
                        contract_count=1,
                        latest_year=year,
                    )

            # If last page returned fewer items than page_size, we're done
            if len(items) < page_size:
                break
            page += 1

        print(f"[BNDES] Discovery complete: {len(aggregated)} companies found")
        companies = self._rank(list(aggregated.values()))
        return companies[:limit]

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
