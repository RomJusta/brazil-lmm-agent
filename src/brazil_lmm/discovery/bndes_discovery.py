"""
BNDES Discovery — finds candidate companies via two sources:

1. BNDES REST API (operacoes.bndes.gov.br) — direct, best data
2. Portal da Transparência Federal — fallback, globally accessible

Both return companies that already use BNDES credit — the warmest leads
for a financing offer since they already understand structured credit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from brazil_lmm.discovery.sector_map import INDUSTRY_CNAES, HEALTH_CNAES


BNDES_API_URL       = "https://operacoes.bndes.gov.br/api/operacoes"
TRANSPARENCIA_URL   = "https://api.portaldatransparencia.gov.br/api-de-dados/contratos"

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
    score_hint: float = 0.0


class BNDESDiscovery:
    def __init__(self, transparencia_api_key: str | None = None) -> None:
        import os
        self._api_key = transparencia_api_key or os.getenv("TRANSPARENCIA_API_KEY", "")
        self._client = httpx.AsyncClient(
            timeout=60.0,
            follow_redirects=True,
            headers={"User-Agent": "BrazilLMMAgent/1.0"},
        )

    async def discover(
        self,
        sectors: list[str],
        ufs: list[str] | None = None,
        limit: int = 500,
    ) -> list[DiscoveredCompany]:
        allowed_cnaes = self._sector_cnaes(sectors)

        # Try BNDES API first
        companies = await self._fetch_bndes_api(allowed_cnaes, ufs, limit)

        # Fallback: Portal da Transparência
        if not companies and self._api_key:
            print("[BNDES] Falling back to Portal da Transparência")
            companies = await self._fetch_transparencia(allowed_cnaes, ufs, limit)

        if not companies:
            print("[BNDES] No companies found. Check network access or add TRANSPARENCIA_API_KEY.")

        return self._rank(companies)[:limit]

    # -----------------------------------------------------------------------
    # Source 1: BNDES API
    # -----------------------------------------------------------------------

    async def _fetch_bndes_api(
        self,
        allowed_cnaes: set[str],
        ufs: list[str] | None,
        limit: int,
    ) -> list[DiscoveredCompany]:
        aggregated: dict[str, DiscoveredCompany] = {}
        page = 1

        while len(aggregated) < limit:
            params: dict = {"size": 100, "pagina": page}
            try:
                resp = await self._client.get(BNDES_API_URL, params=params, timeout=20.0)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[BNDES] API unreachable: {e}")
                break

            items = data if isinstance(data, list) else data.get("content", [])
            if not items:
                break

            if page == 1:
                print(f"[BNDES] API fields: {list(items[0].keys())[:10]}")

            self._process_items(items, aggregated, allowed_cnaes, ufs)

            if len(items) < 100:
                break
            page += 1

        print(f"[BNDES] API: {len(aggregated)} companies")
        return list(aggregated.values())

    def _process_items(
        self,
        items: list[dict],
        aggregated: dict[str, DiscoveredCompany],
        allowed_cnaes: set[str],
        ufs: list[str] | None,
    ) -> None:
        for item in items:
            cnpj = re.sub(r"\D", "", str(
                item.get("cnpjBeneficiarioFinal") or
                item.get("cnpj") or ""
            ))
            if len(cnpj) != 14:
                continue

            uf = (item.get("uf") or item.get("ufBeneficiario") or "").strip().upper()
            if ufs and uf not in [u.upper() for u in ufs]:
                continue

            cnae_raw = str(item.get("setorCnae") or item.get("setor") or item.get("cnae") or "")
            cnae_prefix = re.sub(r"\D", "", cnae_raw)[:2]
            if allowed_cnaes and cnae_prefix not in allowed_cnaes:
                continue

            try:
                value = float(item.get("valorContratado") or item.get("valor") or 0)
            except (ValueError, TypeError):
                value = 0.0

            if value < MIN_CONTRACT_BRL or value > MAX_CONTRACT_BRL:
                continue

            razao = str(item.get("nomeCliente") or item.get("razaoSocial") or item.get("beneficiario") or "")
            city  = str(item.get("municipio") or "").title()
            date  = str(item.get("dataContratacao") or item.get("data") or "")
            m     = re.search(r"\d{4}", date)
            year  = int(m.group()) if m else None

            if cnpj in aggregated:
                aggregated[cnpj].total_bndes_brl += value
                aggregated[cnpj].contract_count += 1
                if year and (aggregated[cnpj].latest_year is None or year > aggregated[cnpj].latest_year):
                    aggregated[cnpj].latest_year = year
            else:
                aggregated[cnpj] = DiscoveredCompany(
                    cnpj=cnpj, razao_social=razao, sector_hint=cnae_raw,
                    uf=uf, city=city, total_bndes_brl=value,
                    contract_count=1, latest_year=year,
                )

    # -----------------------------------------------------------------------
    # Source 2: Portal da Transparência (requires free API key)
    # -----------------------------------------------------------------------

    async def _fetch_transparencia(
        self,
        allowed_cnaes: set[str],
        ufs: list[str] | None,
        limit: int,
    ) -> list[DiscoveredCompany]:
        aggregated: dict[str, DiscoveredCompany] = {}
        page = 1
        headers = {"chave-api-dados": self._api_key}

        while len(aggregated) < limit:
            try:
                resp = await self._client.get(
                    TRANSPARENCIA_URL,
                    params={
                        "codigoOrgao": "36200",   # BNDES organ code
                        "pagina": page,
                    },
                    headers=headers,
                    timeout=30.0,
                )
                resp.raise_for_status()
                items = resp.json()
            except Exception as e:
                print(f"[TRANSPARENCIA] Error page {page}: {e}")
                break

            if not items:
                break

            if page == 1:
                print(f"[TRANSPARENCIA] Fields: {list(items[0].keys())[:10]}")

            for item in items:
                contratado = item.get("contratado", {}) or {}
                cnpj = re.sub(r"\D", "", str(contratado.get("cnpj") or ""))
                if len(cnpj) != 14:
                    continue

                try:
                    value = float(item.get("valorInicial") or 0)
                except (ValueError, TypeError):
                    value = 0.0

                if value < MIN_CONTRACT_BRL or value > MAX_CONTRACT_BRL:
                    continue

                razao = str(contratado.get("nome") or "")
                date  = str(item.get("dataAssinatura") or "")
                m     = re.search(r"\d{4}", date)
                year  = int(m.group()) if m else None

                if cnpj not in aggregated:
                    aggregated[cnpj] = DiscoveredCompany(
                        cnpj=cnpj, razao_social=razao, sector_hint="",
                        uf="", city="", total_bndes_brl=value,
                        contract_count=1, latest_year=year,
                    )
                else:
                    aggregated[cnpj].total_bndes_brl += value
                    aggregated[cnpj].contract_count += 1

            if len(items) < 20:
                break
            page += 1

        print(f"[TRANSPARENCIA] {len(aggregated)} companies found")
        return list(aggregated.values())

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _rank(self, companies: list[DiscoveredCompany]) -> list[DiscoveredCompany]:
        for c in companies:
            score = 0.0
            score += min(c.contract_count / 10, 0.3)
            score += min(c.total_bndes_brl / 80_000_000, 0.4)
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
