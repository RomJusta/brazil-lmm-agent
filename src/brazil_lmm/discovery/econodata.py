"""
Econodata scraping discovery.

Econodata (econodata.com.br) is a Brazilian B2B company directory
searchable by sector (CNAE) and state. The public pages list company
names and CNPJs without authentication.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from brazil_lmm.discovery.bndes_discovery import DiscoveredCompany
from brazil_lmm.discovery.sector_map import INDUSTRY_CNAES, HEALTH_CNAES

BASE_URL = "https://www.econodata.com.br/maiores-empresas"

# Econodata sector slugs mapped from our CNAE groups
SECTOR_SLUGS = {
    "industria": [
        "industria-de-alimentos", "industria-quimica", "industria-metalurgica",
        "industria-de-maquinas-e-equipamentos", "industria-farmaceutica",
        "industria-de-plasticos-e-borrachas", "industria-textil",
        "industria-de-veiculos", "industria-de-papel-e-celulose",
        "construcao-civil",
    ],
    "saude": [
        "hospitais-e-clinicas", "planos-de-saude", "industria-farmaceutica",
        "laboratorios-de-analises-clinicas", "servicos-de-saude",
    ],
}

UF_SLUGS = {
    "SP": "sao-paulo", "RJ": "rio-de-janeiro", "MG": "minas-gerais",
    "RS": "rio-grande-do-sul", "PR": "parana", "SC": "santa-catarina",
    "BA": "bahia", "GO": "goias", "PE": "pernambuco", "CE": "ceara",
    "DF": "distrito-federal", "ES": "espirito-santo",
}


class EconodataDiscovery:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )

    async def discover(
        self,
        sectors: list[str],
        ufs: list[str] | None = None,
        limit: int = 200,
    ) -> list[DiscoveredCompany]:
        slugs: list[str] = []
        for s in sectors:
            key = "industria" if "industria" in s.lower() or "indústria" in s.lower() else "saude"
            slugs.extend(SECTOR_SLUGS.get(key, []))
        slugs = list(dict.fromkeys(slugs))  # deduplicate

        uf_list = [UF_SLUGS.get(u.upper(), u.lower()) for u in (ufs or [])]
        if not uf_list:
            uf_list = ["brasil"]

        tasks = [
            self._scrape_page(sector_slug, uf_slug)
            for sector_slug in slugs[:5]   # cap to avoid overloading
            for uf_slug in uf_list[:3]
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        seen: set[str] = set()
        companies: list[DiscoveredCompany] = []
        for batch in results:
            if isinstance(batch, Exception):
                continue
            for c in batch:
                if c.cnpj not in seen:
                    companies.append(c)
                    seen.add(c.cnpj)
                    if len(companies) >= limit:
                        break

        print(f"[ECONODATA] Found {len(companies)} companies")
        return companies

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
    async def _scrape_page(self, sector_slug: str, uf_slug: str) -> list[DiscoveredCompany]:
        url = f"{BASE_URL}/{uf_slug}/{sector_slug}"
        companies: list[DiscoveredCompany] = []
        try:
            resp = await self._client.get(url)
            if resp.status_code != 200:
                return []
            html = resp.text

            # Extract CNPJs — they appear as 00.000.000/0000-00 in the HTML
            cnpjs = re.findall(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", html)
            names = re.findall(
                r'(?:class="[^"]*(?:company|empresa|nome)[^"]*"[^>]*>|<h[23][^>]*>)\s*([A-ZÀ-Ú][^<\n]{5,80})',
                html,
            )

            for i, raw_cnpj in enumerate(cnpjs[:50]):
                cnpj = re.sub(r"\D", "", raw_cnpj)
                if len(cnpj) != 14:
                    continue
                name = names[i].strip() if i < len(names) else ""
                companies.append(DiscoveredCompany(
                    cnpj=cnpj,
                    razao_social=name,
                    sector_hint=sector_slug,
                    uf=uf_slug.upper()[:2],
                    city="",
                    total_bndes_brl=0.0,
                    contract_count=0,
                    latest_year=None,
                    discovery_source="econodata",
                    score_hint=0.3,
                ))
        except Exception as e:
            print(f"[ECONODATA] Error {url}: {e}")
        return companies

    async def close(self) -> None:
        await self._client.aclose()
