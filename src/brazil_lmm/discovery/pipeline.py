"""
Discovery Pipeline — end-to-end: find → enrich → rank → export.

Flow:
1. BNDESDiscovery  → CNPJs of companies with existing credit (warmest leads)
2. RFBBulkDiscovery → CNPJs of active companies by sector/UF (broader net)
3. Deduplicate, cap at requested limit
4. Enrich each CNPJ through the full enrichment pipeline
5. Score for credit/financing outreach
6. Return ranked Company list
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from brazil_lmm.discovery.bndes_discovery import BNDESDiscovery
from brazil_lmm.discovery.rfb_bulk import RFBBulkDiscovery
from brazil_lmm.models import Company, CompanyQuery


@dataclass
class DiscoveryFilter:
    sectors: list[str] = field(default_factory=lambda: ["industria", "saude"])
    ufs: list[str] | None = None          # None = all Brazil
    limit: int = 200                       # total companies to enrich
    min_outreach_score: float = 0.0
    use_bndes_source: bool = True          # primary: companies already using credit
    use_rfb_source: bool = True            # secondary: broader CNAE-based search


class DiscoveryPipeline:
    def __init__(self, orchestrator) -> None:  # type: ignore[annotation-unchecked]
        self._orchestrator = orchestrator

    async def run(self, filter: DiscoveryFilter) -> list[Company]:
        """
        Full pipeline: discover → enrich → rank.
        Returns companies sorted by outreach_score descending.
        """
        # Phase 1: Discovery — collect candidate CNPJs
        candidates = await self._discover(filter)

        # Phase 2: Enrichment — run full pipeline on each CNPJ
        queries = [
            CompanyQuery(cnpj=c.cnpj, company_name=getattr(c, "razao_social", None))
            for c in candidates
        ]
        companies = await self._orchestrator.process_batch(queries)

        # Phase 3: Filter and rank
        companies = [
            c for c in companies
            if c.size_tier == "LMM" and c.is_active
            and (c.outreach_score or 0) >= filter.min_outreach_score
        ]
        companies.sort(key=lambda c: c.outreach_score or 0, reverse=True)

        return companies

    async def _discover(self, filter: DiscoveryFilter) -> list:
        seen_cnpjs: set[str] = set()
        candidates = []

        # BNDES source first — these are the warmest leads (already credit users)
        if filter.use_bndes_source:
            import os
            bndes = BNDESDiscovery(transparencia_api_key=os.getenv("TRANSPARENCIA_API_KEY"))
            try:
                bndes_results = await bndes.discover(
                    sectors=filter.sectors,
                    ufs=filter.ufs,
                    limit=filter.limit,
                )
                for c in bndes_results:
                    if c.cnpj not in seen_cnpjs:
                        candidates.append(c)
                        seen_cnpjs.add(c.cnpj)
            finally:
                await bndes.close()

        # RFB source second — fills in companies not in BNDES
        remaining = filter.limit - len(candidates)
        if filter.use_rfb_source and remaining > 0:
            rfb = RFBBulkDiscovery()
            try:
                rfb_results = await rfb.discover(
                    sectors=filter.sectors,
                    ufs=filter.ufs,
                    limit=remaining * 2,
                )
                for c in rfb_results:
                    if c.cnpj not in seen_cnpjs and len(candidates) < filter.limit:
                        candidates.append(c)
                        seen_cnpjs.add(c.cnpj)
            finally:
                await rfb.close()

        return candidates[: filter.limit]
