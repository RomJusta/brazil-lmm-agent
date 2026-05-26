"""
Orchestrator — coordinates all enrichment agents, merges partial results,
uses Claude (tool-use) for disambiguation, and scores companies for outreach.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any

import anthropic

from brazil_lmm.agents.bndes import BNDESAgent
from brazil_lmm.agents.finep import FINEPAgent
from brazil_lmm.agents.linkedin import LinkedInAgent
from brazil_lmm.agents.receita_federal import ReceitaFederalAgent
from brazil_lmm.agents.tech_stack import TechStackAgent
from brazil_lmm.models import (
    Company,
    CompanyQuery,
    FinancialSnapshot,
    PartialCompany,
    TechStack,
    cnae_to_sector,
)


# ---------------------------------------------------------------------------
# Outreach scoring weights
# ---------------------------------------------------------------------------

SCORE_WEIGHTS = {
    # Offer: Crédito / Financiamento — Indústria + Saúde
    "is_lmm": 25,               # revenue bracket R$50M–R$850M
    "has_bndes_credit": 25,     # already uses structured credit = easiest to convert
    "has_finep_credit": 15,     # innovation credit = open to new instruments
    "active_company": 15,       # not dormant
    "has_ceo_contact": 10,      # can reach decision maker directly
    "target_sector": 10,        # Indústria or Saúde = primary targets
}


class Orchestrator:
    def __init__(
        self,
        *,
        anthropic_api_key: str | None = None,
        transparencia_api_key: str | None = None,
        builtwith_api_key: str | None = None,
        linkedin_cookie: str | None = None,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=anthropic_api_key or os.environ["ANTHROPIC_API_KEY"]
        )
        self._transparencia_key = transparencia_api_key or os.getenv("TRANSPARENCIA_API_KEY")
        self._builtwith_key = builtwith_api_key or os.getenv("BUILTWITH_API_KEY")
        self._linkedin_cookie = linkedin_cookie or os.getenv("LINKEDIN_LI_AT_COOKIE")

    async def process(self, query: CompanyQuery) -> Company:
        """Full enrichment pipeline for a single company query."""
        cnpj = query.cnpj or ""
        name = query.company_name or ""

        # Phase 1: official sources in parallel (fast, no auth needed)
        rf_task = asyncio.create_task(self._run_receita_federal(cnpj, name))
        bndes_task = asyncio.create_task(self._run_bndes(cnpj))
        finep_task = asyncio.create_task(self._run_finep(cnpj, name))

        rf_partial, bndes_partial, finep_partial = await asyncio.gather(
            rf_task, bndes_task, finep_task
        )

        # Build intermediate company from official data
        company = self._merge([rf_partial, bndes_partial, finep_partial], cnpj)

        # Phase 2: web-based enrichment (needs website URL)
        website = company.website or query.website
        linkedin_task = asyncio.create_task(
            self._run_linkedin(cnpj, name)
        )
        tech_task = asyncio.create_task(
            self._run_tech_stack(cnpj, website or "")
        )
        linkedin_partial, tech_partial = await asyncio.gather(linkedin_task, tech_task)

        company = self._merge_into(company, [linkedin_partial, tech_partial])

        # Phase 3: Claude disambiguation for ambiguous / conflicting fields
        company = await self._claude_disambiguate(company, query)

        # Phase 4: derive computed fields
        company.sector = company.sector or company.derive_sector()
        company.size_tier = company.compute_size_tier()
        company.outreach_score = self._score(company)
        company.last_enriched_at = datetime.utcnow()

        return company

    async def process_batch(self, queries: list[CompanyQuery]) -> list[Company]:
        """Process multiple companies with controlled concurrency."""
        semaphore = asyncio.Semaphore(5)  # max 5 companies in parallel

        async def bounded(q: CompanyQuery) -> Company:
            async with semaphore:
                return await self.process(q)

        return await asyncio.gather(*[bounded(q) for q in queries])

    # -----------------------------------------------------------------------
    # Agent runners
    # -----------------------------------------------------------------------

    async def _run_receita_federal(self, cnpj: str, name: str) -> PartialCompany:
        async with ReceitaFederalAgent() as agent:
            return await agent.enrich(cnpj, name)

    async def _run_bndes(self, cnpj: str) -> PartialCompany:
        async with BNDESAgent() as agent:
            return await agent.enrich(cnpj)

    async def _run_finep(self, cnpj: str, name: str) -> PartialCompany:
        async with FINEPAgent(self._transparencia_key) as agent:
            return await agent.enrich(cnpj, name)

    async def _run_linkedin(self, cnpj: str, name: str) -> PartialCompany:
        async with LinkedInAgent(self._linkedin_cookie) as agent:
            return await agent.enrich(cnpj, name)

    async def _run_tech_stack(self, cnpj: str, website: str) -> PartialCompany:
        # Pass website as company_name — TechStackAgent checks for http prefix
        async with TechStackAgent(self._builtwith_key) as agent:
            return await agent.enrich(cnpj, website)

    # -----------------------------------------------------------------------
    # Merge logic
    # -----------------------------------------------------------------------

    def _merge(self, partials: list[PartialCompany], cnpj: str) -> Company:
        company = Company(cnpj=cnpj, razao_social="")
        return self._merge_into(company, partials)

    def _merge_into(self, company: Company, partials: list[PartialCompany]) -> Company:
        """
        Merge partials into company, preferring higher-confidence values.
        Official sources (receita_federal confidence=0.9) beat inferred ones.
        """
        for partial in partials:
            if partial.confidence == 0.0:
                continue

            # Scalar fields — only overwrite if we have a better or missing value
            for field in [
                "razao_social", "nome_fantasia", "cnae_primary", "sector",
                "founded_year", "website", "linkedin_url", "address_city",
                "address_uf", "is_active",
            ]:
                current = getattr(company, field, None)
                incoming = getattr(partial, field, None)
                if incoming is not None and (current is None or current == ""):
                    setattr(company, field, incoming)

            # CEO — prefer LinkedIn (has profile URL) over QSA
            if partial.ceo:
                if company.ceo is None:
                    company.ceo = partial.ceo
                elif partial.source == "linkedin" and company.ceo.source != "linkedin":
                    company.ceo = partial.ceo

            # Owners — deduplicate by name
            existing_names = {o.name for o in company.owners}
            for owner in partial.owners:
                if owner.name not in existing_names:
                    company.owners.append(owner)
                    existing_names.add(owner.name)

            # BNDES contracts — deduplicate by contract number
            existing_ids = {c.contract_number for c in company.bndes_contracts}
            for contract in partial.bndes_contracts:
                if contract.contract_number not in existing_ids:
                    company.bndes_contracts.append(contract)
                    existing_ids.add(contract.contract_number)

            # FINEP contracts — deduplicate by program + year
            existing_finep = {(c.program, c.year) for c in company.finep_contracts}
            for contract in partial.finep_contracts:
                key = (contract.program, contract.year)
                if key not in existing_finep:
                    company.finep_contracts.append(contract)
                    existing_finep.add(key)

            # Financials — merge fields individually
            if partial.financials:
                self._merge_financials(company.financials, partial.financials)

            # Tech stack — merge lists
            if partial.tech_stack:
                self._merge_tech_stack(company.tech_stack, partial.tech_stack)

            # Track sources
            if partial.source not in company.enrichment_sources:
                company.enrichment_sources.append(partial.source)

        # Compute overall confidence as weighted average of populated fields
        company.confidence_score = self._compute_confidence(company)
        return company

    @staticmethod
    def _merge_financials(target: FinancialSnapshot, source: FinancialSnapshot) -> None:
        for field in ["revenue_brl", "ebitda_brl", "ebitda_margin", "net_profit_brl", "headcount"]:
            if getattr(target, field) is None and getattr(source, field) is not None:
                setattr(target, field, getattr(source, field))
        if target.reference_year is None:
            target.reference_year = source.reference_year
        if target.source is None:
            target.source = source.source

    @staticmethod
    def _merge_tech_stack(target: TechStack, source: TechStack) -> None:
        for field in ["erp", "crm", "cloud_providers", "ecommerce", "analytics", "cybersecurity", "other"]:
            existing = getattr(target, field)
            for item in getattr(source, field):
                if item not in existing:
                    existing.append(item)
        for src in source.inferred_from:
            if src not in target.inferred_from:
                target.inferred_from.append(src)

    @staticmethod
    def _compute_confidence(company: Company) -> float:
        fields = {
            "razao_social": company.razao_social != "",
            "sector": company.sector is not None,
            "ceo": company.ceo is not None,
            "owners": len(company.owners) > 0,
            "financials": company.financials.revenue_brl is not None or company.financials.headcount is not None,
            "bndes": True,  # always attempted
            "finep": True,
            "tech_stack": len(company.tech_stack.inferred_from) > 0,
        }
        return sum(1 for v in fields.values() if v) / len(fields)

    # -----------------------------------------------------------------------
    # Claude disambiguation
    # -----------------------------------------------------------------------

    async def _claude_disambiguate(self, company: Company, query: CompanyQuery) -> Company:
        """
        Ask Claude to resolve ambiguous fields and fill gaps using tool-use.
        Claude can call back into the data we already have to reason about it.
        """
        company_json = company.model_dump_json(indent=2)

        tools = [
            {
                "name": "update_company_field",
                "description": "Update a specific field on the company record with a resolved value.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "field": {
                            "type": "string",
                            "description": "The field path to update (e.g. 'sector', 'ceo.role', 'size_tier')",
                        },
                        "value": {
                            "description": "The resolved value for the field.",
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "One sentence explaining why this value was chosen.",
                        },
                    },
                    "required": ["field", "value", "reasoning"],
                },
            },
            {
                "name": "add_outreach_notes",
                "description": "Add a brief outreach note summarizing why this company is or isn't a good fit.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "notes": {
                            "type": "string",
                            "description": "2-3 sentences on commercial outreach fit.",
                        },
                    },
                    "required": ["notes"],
                },
            },
        ]

        system = (
            "You are a Brazilian commercial intelligence analyst. "
            "You receive a partially enriched company record and must:\n"
            "1. Resolve ambiguous or missing fields using available evidence.\n"
            "2. Infer the sector from CNAE if missing.\n"
            "3. Determine if the CEO field is correctly attributed.\n"
            "4. Write outreach notes in Portuguese explaining fit for a commercial approach.\n"
            "Use the provided tools to update the record. Only call tools when you have "
            "sufficient evidence — do not fabricate data."
        )

        prompt = (
            f"Company record to review:\n```json\n{company_json}\n```\n\n"
            "Identify and resolve any ambiguous or missing fields. "
            "Then write outreach notes summarizing commercial fit."
        )

        updates: dict[str, Any] = {}
        outreach_notes: str | None = None

        response = await self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system,
            tools=tools,  # type: ignore[arg-type]
            messages=[{"role": "user", "content": prompt}],
        )

        for block in response.content:
            if block.type == "tool_use":
                if block.name == "update_company_field":
                    updates[block.input["field"]] = block.input["value"]
                elif block.name == "add_outreach_notes":
                    outreach_notes = block.input["notes"]

        # Apply updates
        for field_path, value in updates.items():
            self._apply_field_update(company, field_path, value)

        if outreach_notes:
            company.outreach_notes = outreach_notes

        return company

    @staticmethod
    def _apply_field_update(company: Company, field_path: str, value: Any) -> None:
        parts = field_path.split(".")
        obj: Any = company
        for part in parts[:-1]:
            obj = getattr(obj, part, None)
            if obj is None:
                return
        try:
            setattr(obj, parts[-1], value)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Outreach scoring
    # -----------------------------------------------------------------------

    TARGET_SECTORS = {
        "Indústria", "Alimentos e Bebidas", "Química", "Farmacêutico",
        "Máquinas e Equipamentos", "Metalurgia", "Veículos", "Construção Civil",
        "Borracha e Plástico", "Eletrônicos", "Têxtil",
        "Saúde", "Equipamentos Médicos",
    }

    def _score(self, company: Company) -> float:
        score = 0.0
        max_score = sum(SCORE_WEIGHTS.values())

        if company.size_tier == "LMM":
            score += SCORE_WEIGHTS["is_lmm"]
        if company.bndes_contracts:
            score += SCORE_WEIGHTS["has_bndes_credit"]
        if company.finep_contracts:
            score += SCORE_WEIGHTS["has_finep_credit"]
        if company.ceo is not None:
            score += SCORE_WEIGHTS["has_ceo_contact"]
        if company.is_active:
            score += SCORE_WEIGHTS["active_company"]
        if company.sector and any(t in (company.sector or "") for t in self.TARGET_SECTORS):
            score += SCORE_WEIGHTS["target_sector"]

        return round(score / max_score, 3)
