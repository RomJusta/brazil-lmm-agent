"""
Discovery Pipeline — runs all sources in parallel, merges, enriches, ranks.

Sources (all run concurrently):
  1. BNDES API          — direct REST (works when Railway can reach bndes.gov.br)
  2. Portal da Transparência — BNDES + FINEP contracts (requires TRANSPARENCIA_API_KEY)
  3. Econodata scraping — Brazilian B2B directory, no auth needed
  4. Seed list          — curated 40 known LMM companies, instant fallback
  5. BNDES file upload  — user-uploaded spreadsheet (separate endpoint)

Results are deduplicated by CNPJ, then enriched and ranked.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from brazil_lmm.discovery.bndes_discovery import BNDESDiscovery, DiscoveredCompany
from brazil_lmm.discovery.econodata import EconodataDiscovery
from brazil_lmm.discovery.transparencia import TransparenciaDiscovery
from brazil_lmm.discovery.seed_list import get_seed_companies
from brazil_lmm.models import Company, CompanyQuery


@dataclass
class DiscoveryFilter:
    sectors: list[str] = field(default_factory=lambda: ["industria", "saude"])
    ufs: list[str] | None = None
    limit: int = 200
    min_outreach_score: float = 0.0
    use_bndes_source: bool = True
    use_rfb_source: bool = False
    use_econodata: bool = True
    use_transparencia: bool = True
    use_seed: bool = True


class DiscoveryPipeline:
    def __init__(self, orchestrator) -> None:
        self._orchestrator = orchestrator

    async def run(self, f: DiscoveryFilter) -> list[Company]:
        # Phase 1: all discovery sources in parallel
        candidates = await self._discover_all(f)

        if not candidates:
            print("[PIPELINE] No candidates found from any source.")
            return []

        print(f"[PIPELINE] {len(candidates)} unique candidates → enriching...")

        # Phase 2: enrich
        queries = [
            CompanyQuery(cnpj=c.cnpj, company_name=c.razao_social or None)
            for c in candidates
        ]
        companies = await self._orchestrator.process_batch(queries)

        # Phase 3: filter + rank
        companies = [
            c for c in companies
            if c.is_active
            and (c.outreach_score or 0) >= f.min_outreach_score
        ]
        companies.sort(key=lambda c: c.outreach_score or 0, reverse=True)
        print(f"[PIPELINE] {len(companies)} companies after enrichment + filter")
        return companies

    async def _discover_all(self, f: DiscoveryFilter) -> list[DiscoveredCompany]:
        tasks = []

        if f.use_bndes_source:
            tasks.append(self._run_bndes(f))
        if f.use_transparencia:
            tasks.append(self._run_transparencia(f))
        if f.use_econodata:
            tasks.append(self._run_econodata(f))
        if f.use_seed:
            tasks.append(self._run_seed(f))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        seen: set[str] = set()
        merged: list[DiscoveredCompany] = []

        for batch in results:
            if isinstance(batch, Exception):
                print(f"[PIPELINE] Source error: {batch}")
                continue
            for c in batch:
                if c.cnpj not in seen:
                    merged.append(c)
                    seen.add(c.cnpj)

        # Sort by score_hint so best candidates are enriched first
        merged.sort(key=lambda x: x.score_hint, reverse=True)
        limited = merged[: f.limit]
        print(f"[PIPELINE] {len(merged)} total candidates, using top {len(limited)}")
        return limited

    async def _run_bndes(self, f: DiscoveryFilter) -> list[DiscoveredCompany]:
        agent = BNDESDiscovery(transparencia_api_key=os.getenv("TRANSPARENCIA_API_KEY"))
        try:
            return await agent.discover(f.sectors, f.ufs, f.limit)
        finally:
            await agent.close()

    async def _run_transparencia(self, f: DiscoveryFilter) -> list[DiscoveredCompany]:
        key = os.getenv("TRANSPARENCIA_API_KEY", "")
        if not key:
            print("[TRANSPARENCIA] No API key — skipping. Add TRANSPARENCIA_API_KEY to Railway.")
            return []
        agent = TransparenciaDiscovery(key)
        try:
            return await agent.discover(f.ufs, f.limit)
        finally:
            await agent.close()

    async def _run_econodata(self, f: DiscoveryFilter) -> list[DiscoveredCompany]:
        agent = EconodataDiscovery()
        try:
            return await agent.discover(f.sectors, f.ufs, f.limit)
        finally:
            await agent.close()

    async def _run_seed(self, f: DiscoveryFilter) -> list[DiscoveredCompany]:
        return get_seed_companies(f.sectors, f.ufs)
