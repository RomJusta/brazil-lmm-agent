"""
Portal da Transparência discovery.

Queries BNDES and FINEP contracts via the official Federal Transparency API.
Globally accessible. Requires a free API key (instant at portaldatransparencia.gov.br).

BNDES organ code : 36200
FINEP organ code : 24201
"""
from __future__ import annotations

import re

import httpx

from brazil_lmm.discovery.bndes_discovery import DiscoveredCompany

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados/contratos"
MIN_VALUE = 500_000
MAX_VALUE = 80_000_000


class TransparenciaDiscovery:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"chave-api-dados": api_key},
        )

    async def discover(
        self,
        ufs: list[str] | None = None,
        limit: int = 300,
    ) -> list[DiscoveredCompany]:
        companies: dict[str, DiscoveredCompany] = {}

        for organ_code, source_name in [("36200", "bndes"), ("24201", "finep")]:
            page = 1
            while len(companies) < limit:
                try:
                    resp = await self._client.get(
                        BASE_URL,
                        params={"codigoOrgao": organ_code, "pagina": page},
                    )
                    resp.raise_for_status()
                    items = resp.json()
                except Exception as e:
                    print(f"[TRANSPARENCIA] {source_name} page {page} error: {e}")
                    break

                if not items:
                    break

                if page == 1:
                    print(f"[TRANSPARENCIA] {source_name} fields: {list(items[0].keys())[:8]}")

                for item in items:
                    contratado = item.get("contratado") or {}
                    cnpj = re.sub(r"\D", "", str(contratado.get("cnpj") or ""))
                    if len(cnpj) != 14:
                        continue

                    try:
                        value = float(item.get("valorInicial") or 0)
                    except (ValueError, TypeError):
                        value = 0.0

                    if value < MIN_VALUE or value > MAX_VALUE:
                        continue

                    razao = str(contratado.get("nome") or "")
                    date  = str(item.get("dataAssinatura") or "")
                    m     = re.search(r"\d{4}", date)
                    year  = int(m.group()) if m else None

                    if cnpj not in companies:
                        companies[cnpj] = DiscoveredCompany(
                            cnpj=cnpj, razao_social=razao, sector_hint="",
                            uf="", city="", total_bndes_brl=value,
                            contract_count=1, latest_year=year,
                            discovery_source=source_name,
                        )
                    else:
                        companies[cnpj].total_bndes_brl += value
                        companies[cnpj].contract_count += 1

                if len(items) < 20:
                    break
                page += 1

        print(f"[TRANSPARENCIA] {len(companies)} companies found")
        return list(companies.values())[:limit]

    async def close(self) -> None:
        await self._client.aclose()
