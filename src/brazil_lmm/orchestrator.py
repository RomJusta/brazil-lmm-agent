"""
Orchestrator — coordinates all enrichment agents, merges partial results,
uses Claude (tool-use) for disambiguation, and scores companies for outreach.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

from google import genai
from google.genai import types as genai_types

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
    # Nota: pesos somam 100. Score normalizado 0–1.
    # Sem API keys externas máximo atingível é (active+sector+lmm) = até 55 pts
    "is_lmm": 25,               # receita verificada R$50M–R$850M
    "has_bndes_credit": 20,     # já usa crédito estruturado = lead mais quente
    "has_finep_credit": 10,     # usa fomento à inovação
    "active_company": 20,       # empresa ativa (sempre checado)
    "has_ceo_contact": 10,      # acesso ao decisor
    "target_sector": 15,        # Indústria ou Saúde = alvo primário
}


class Orchestrator:
    def __init__(
        self,
        *,
        google_api_key: str | None = None,
        transparencia_api_key: str | None = None,
        builtwith_api_key: str | None = None,
        linkedin_cookie: str | None = None,
    ) -> None:
        api_key = google_api_key or os.getenv("GOOGLE_API_KEY", "")
        self._genai = genai.Client(api_key=api_key) if api_key else None
        self._transparencia_key = transparencia_api_key or os.getenv("TRANSPARENCIA_API_KEY")
        self._builtwith_key = builtwith_api_key or os.getenv("BUILTWITH_API_KEY")
        self._linkedin_cookie = linkedin_cookie or os.getenv("LINKEDIN_LI_AT_COOKIE")

    async def process(self, query: CompanyQuery) -> Company:
        """Full enrichment pipeline for a single company query."""
        cnpj = query.cnpj or ""
        name = query.company_name or ""

        # Phase 1: official sources in parallel — errors are caught per-agent
        rf_partial, bndes_partial, finep_partial = await asyncio.gather(
            self._run_receita_federal(cnpj, name),
            self._run_bndes(cnpj),
            self._run_finep(cnpj, name),
            return_exceptions=False,
        )

        # Build intermediate company from official data
        company = self._merge([rf_partial, bndes_partial, finep_partial], cnpj)

        # Se veio receita verificada da CVM, usar como base financeira
        if query.revenue_hint and company.financials.revenue_brl is None:
            company.financials.revenue_brl = query.revenue_hint
            company.financials.source = "CVM DFP"
            company.financials.confidence = 0.95

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
        try:
            async with ReceitaFederalAgent() as agent:
                return await agent.enrich(cnpj, name)
        except Exception as e:
            print(f"[RF] Error {cnpj}: {e}")
            return PartialCompany(cnpj=cnpj, source="receita_federal", confidence=0.0)

    async def _run_bndes(self, cnpj: str) -> PartialCompany:
        try:
            async with BNDESAgent() as agent:
                return await agent.enrich(cnpj)
        except Exception as e:
            print(f"[BNDES_ENRICH] Error {cnpj}: {e}")
            return PartialCompany(cnpj=cnpj, source="bndes", confidence=0.0)

    async def _run_finep(self, cnpj: str, name: str) -> PartialCompany:
        try:
            async with FINEPAgent(self._transparencia_key) as agent:
                return await agent.enrich(cnpj, name)
        except Exception as e:
            print(f"[FINEP] Error {cnpj}: {e}")
            return PartialCompany(cnpj=cnpj, source="finep", confidence=0.0)

    async def _run_linkedin(self, cnpj: str, name: str) -> PartialCompany:
        try:
            async with LinkedInAgent(self._linkedin_cookie) as agent:
                return await agent.enrich(cnpj, name)
        except Exception as e:
            return PartialCompany(cnpj=cnpj, source="linkedin", confidence=0.0)

    async def _run_tech_stack(self, cnpj: str, website: str) -> PartialCompany:
        try:
            async with TechStackAgent(self._builtwith_key) as agent:
                return await agent.enrich(cnpj, website)
        except Exception as e:
            return PartialCompany(cnpj=cnpj, source="tech_stack", confidence=0.0)

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

    # Referência de programas BNDES/FINEP embutida no prompt
    _PROGRAMS_REFERENCE = """
PROGRAMAS BNDES DISPONÍVEIS:
- BNDES Automático: até R$150M, capex geral (máquinas, instalações, TI, expansão)
- BNDES FINEM: acima de R$10M, projetos estruturados de expansão/modernização
- BNDES Finame: financiamento de máquinas e equipamentos nacionais
- BNDES Inovação: P&D, prototipagem, transformação digital, automação industrial
- BNDES Crédito Verde: eficiência energética, energia renovável, tratamento de resíduos
- BNDES MPME Inovadora: para empresas de menor porte com projetos de inovação
- BNDES Profarma: setor farmacêutico e equipamentos médicos
- BNDES Proengenharia: empresas de engenharia e serviços tecnológicos

PROGRAMAS FINEP DISPONÍVEIS:
- FINEP Subvenção Econômica: recursos NÃO reembolsáveis para P&D (até 60% do projeto)
- FINEP Crédito Inovação: empréstimos com juros abaixo do mercado para inovação
- FINEP RHAE: bolsas para trazer pesquisadores/doutores para dentro da empresa
- FINEP Inovacred: crédito direto para inovação em micro/pequenas empresas
- FINEP Encomendas Tecnológicas: para desenvolvimento de soluções específicas
- FINEP Startup: para startups e spin-offs de empresas maiores

SINAIS DE NECESSIDADE POR SETOR:
- Indústria/Manufatura: automação, indústria 4.0, redução de perdas, eficiência energética
- Farmacêutico: P&D de novos fármacos, bioequivalência, regulatório ANVISA, exportação
- Saúde/Hospitais: digitalização de prontuário, equipamentos, expansão de leitos
- Têxtil: moda circular, tingimento sustentável, fibras técnicas, e-commerce
- Construção: novas tecnologias construtivas (steel frame, off-site), BIM, ESG
- Agro/Insumos: biopesticidas, rastreabilidade, irrigação de precisão
- Logística: TMS, WMS, frota elétrica, automação de armazém
"""

    async def _claude_disambiguate(self, company: Company, query: CompanyQuery) -> Company:
        """
        Usa Gemini para dois objetivos:
        1. Resolver campos ambíguos/ausentes no registro
        2. Gerar racional comercial estruturado (por que abordar, lacunas de inovação,
           programas sugeridos, estrutura de captação, timing)
        """
        if not self._genai:
            return company

        company_json = company.model_dump_json(indent=2)

        tools = [
            genai_types.Tool(function_declarations=[
                genai_types.FunctionDeclaration(
                    name="update_company_field",
                    description="Atualiza um campo específico do registro da empresa.",
                    parameters=genai_types.Schema(
                        type="OBJECT",
                        properties={
                            "field": genai_types.Schema(type="STRING", description="Caminho do campo, ex: 'sector'"),
                            "value": genai_types.Schema(type="STRING", description="Valor resolvido"),
                            "reasoning": genai_types.Schema(type="STRING", description="Justificativa em uma frase"),
                        },
                        required=["field", "value", "reasoning"],
                    ),
                ),
                genai_types.FunctionDeclaration(
                    name="set_commercial_rationale",
                    description=(
                        "Define o racional comercial completo para abordagem desta empresa "
                        "com uma oferta de captação/financiamento via BNDES ou FINEP."
                    ),
                    parameters=genai_types.Schema(
                        type="OBJECT",
                        properties={
                            "why_approach": genai_types.Schema(
                                type="STRING",
                                description=(
                                    "2-3 frases em português explicando POR QUÊ abordar esta empresa "
                                    "AGORA para uma oferta de crédito/fomento. Baseie-se em: setor, "
                                    "porte, histórico BNDES/FINEP, CNAE, cidade/estado. "
                                    "Seja específico ao contexto desta empresa, não genérico."
                                ),
                            ),
                            "innovation_needs": genai_types.Schema(
                                type="STRING",
                                description=(
                                    "2-3 frases descrevendo as LACUNAS DE INOVAÇÃO prováveis desta empresa "
                                    "com base no setor e CNAE. Ex: automação industrial, transformação digital, "
                                    "eficiência energética, P&D de produtos, expansão de capacidade produtiva. "
                                    "Relacione com o momento do setor no Brasil."
                                ),
                            ),
                            "credit_structure": genai_types.Schema(
                                type="STRING",
                                description=(
                                    "Descreva a ESTRUTURA DE CAPTAÇÃO mais adequada: qual programa, "
                                    "faixa de valor estimada, prazo típico, taxa de juros esperada. "
                                    "Ex: 'BNDES Automático R$5M–R$20M a TJLP+2%a.a. 60 meses para "
                                    "modernização de linha produtiva + FINEP Subvenção 30% do projeto'."
                                ),
                            ),
                            "suggested_programs": genai_types.Schema(
                                type="STRING",
                                description=(
                                    "Lista dos 2-4 programas mais adequados separados por vírgula. "
                                    "Ex: 'BNDES Automático, BNDES Inovação, FINEP Subvenção Econômica'"
                                ),
                            ),
                            "urgency_factors": genai_types.Schema(
                                type="STRING",
                                description=(
                                    "1-2 frases sobre TIMING e urgência: por que agora é um bom momento "
                                    "para esta empresa captar. Ex: ciclo de investimento do setor, "
                                    "janelas de chamadas FINEP abertas, mudanças regulatórias, "
                                    "pressão competitiva, dados macroeconômicos relevantes."
                                ),
                            ),
                        },
                        required=["why_approach", "innovation_needs", "credit_structure",
                                  "suggested_programs", "urgency_factors"],
                    ),
                ),
            ])
        ]

        prompt = (
            "Você é um especialista em estruturação de crédito e fomento à inovação no Brasil, "
            "com profundo conhecimento de BNDES e FINEP.\n\n"
            "Analise o registro abaixo de uma empresa candidata a uma oferta de "
            "captação/financiamento via fomento público (BNDES + FINEP).\n\n"
            "Suas tarefas:\n"
            "1. Se o campo 'sector' estiver vazio, infira pelo CNAE e chame update_company_field.\n"
            "2. SEMPRE chame set_commercial_rationale com análise específica e fundamentada "
            "para ESTA empresa — não use texto genérico, use os dados do registro "
            "(setor, CNAE, cidade, contratos BNDES/FINEP existentes, porte estimado).\n\n"
            "IMPORTANTE: Se a empresa já tem contratos BNDES, isso é sinal POSITIVO "
            "(já usa crédito estruturado = mais fácil converter). Se não tem, "
            "é oportunidade de primeiro crédito.\n\n"
            f"{self._PROGRAMS_REFERENCE}\n\n"
            f"REGISTRO DA EMPRESA:\n```json\n{company_json}\n```"
        )

        updates: dict[str, Any] = {}
        rationale: dict[str, Any] = {}

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._genai.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(tools=tools),
                ),
            )
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    fn_name = part.function_call.name
                    args = dict(part.function_call.args)
                    if fn_name == "update_company_field":
                        updates[args["field"]] = args["value"]
                    elif fn_name == "set_commercial_rationale":
                        rationale = args
        except Exception:
            pass  # Gemini é best-effort; não deixa o enriquecimento falhar

        for field_path, value in updates.items():
            self._apply_field_update(company, field_path, value)

        if rationale:
            company.why_approach = rationale.get("why_approach")
            company.innovation_needs = rationale.get("innovation_needs")
            company.credit_structure = rationale.get("credit_structure")
            company.urgency_factors = rationale.get("urgency_factors")
            # Converte string CSV para lista
            progs_raw = rationale.get("suggested_programs", "")
            company.suggested_programs = [
                p.strip() for p in progs_raw.split(",") if p.strip()
            ]
            # Mantém outreach_notes como resumo concatenado para compatibilidade
            company.outreach_notes = " | ".join(filter(None, [
                rationale.get("why_approach"),
                rationale.get("credit_structure"),
            ]))

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
