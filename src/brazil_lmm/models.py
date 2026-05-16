from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

class Person(BaseModel):
    full_name: str
    role: str  # "CEO", "Diretor", "Sócio-Administrador", etc.
    linkedin_url: str | None = None
    email: str | None = None
    source: str  # which agent populated this


class Owner(BaseModel):
    name: str
    entity_type: Literal["PF", "PJ"]  # individual or company
    ownership_pct: float | None = None
    cnpj_or_cpf_masked: str | None = None  # last 3 digits only for PF
    source: str = "receita_federal_qsa"


# ---------------------------------------------------------------------------
# Public credit lines
# ---------------------------------------------------------------------------

class BNDESContract(BaseModel):
    contract_number: str
    product: str               # "BNDES Finame", "BNDES Automático", etc.
    agent_bank: str | None = None
    value_brl: float
    approval_date: date | None = None
    status: str | None = None
    sector_bndes: str | None = None
    municipality: str | None = None
    uf: str | None = None


class FINEPContract(BaseModel):
    program: str               # "RHAE", "Inova Empresa", "Juro Zero", etc.
    value_brl: float | None = None
    year: int | None = None
    modality: Literal["subvencao", "credito_reembolsavel", "credito_nao_reembolsavel", "unknown"]
    dou_reference: str | None = None
    description: str | None = None


# ---------------------------------------------------------------------------
# Technology stack
# ---------------------------------------------------------------------------

class TechStack(BaseModel):
    erp: list[str] = Field(default_factory=list)
    crm: list[str] = Field(default_factory=list)
    cloud_providers: list[str] = Field(default_factory=list)
    ecommerce: list[str] = Field(default_factory=list)
    analytics: list[str] = Field(default_factory=list)
    cybersecurity: list[str] = Field(default_factory=list)
    other: list[str] = Field(default_factory=list)
    inferred_from: list[str] = Field(default_factory=list)  # ["builtwith", "linkedin_jobs", "website"]


# ---------------------------------------------------------------------------
# Financial snapshot
# ---------------------------------------------------------------------------

class FinancialSnapshot(BaseModel):
    revenue_brl: float | None = None
    ebitda_brl: float | None = None
    ebitda_margin: float | None = None     # 0–1
    net_profit_brl: float | None = None
    headcount: int | None = None
    reference_year: int | None = None
    source: str | None = None              # "CVM", "news_estimate", "orbis"
    confidence: float = 0.5               # 0–1


# ---------------------------------------------------------------------------
# Master company record
# ---------------------------------------------------------------------------

SizeTier = Literal["LMM", "MM", "Large", "Unknown"]

CNAE_TO_SECTOR: dict[str, str] = {
    "01": "Agropecuária",
    "05": "Mineração",
    "10": "Alimentos e Bebidas",
    "13": "Têxtil",
    "19": "Petróleo e Gás",
    "20": "Química",
    "22": "Borracha e Plástico",
    "26": "Eletrônicos",
    "27": "Eletrodomésticos",
    "28": "Máquinas e Equipamentos",
    "29": "Veículos",
    "41": "Construção Civil",
    "46": "Comércio Atacadista",
    "47": "Comércio Varejista",
    "49": "Transporte Terrestre",
    "51": "Transporte Aéreo",
    "52": "Logística",
    "55": "Hotelaria",
    "56": "Alimentação",
    "58": "Editoras",
    "61": "Telecomunicações",
    "62": "TI e Software",
    "63": "Serviços de Dados",
    "64": "Serviços Financeiros",
    "65": "Seguros",
    "66": "Mercado de Capitais",
    "68": "Imobiliário",
    "69": "Jurídico",
    "70": "Consultoria",
    "71": "Engenharia",
    "72": "P&D",
    "73": "Publicidade",
    "74": "Design",
    "75": "Veterinária",
    "77": "Locação",
    "78": "RH",
    "80": "Segurança Privada",
    "82": "Facilities",
    "84": "Governo",
    "85": "Educação",
    "86": "Saúde",
    "87": "Assistência Social",
    "90": "Artes e Cultura",
    "93": "Esporte e Lazer",
    "96": "Serviços Pessoais",
}


def cnae_to_sector(cnae_code: str) -> str:
    prefix = cnae_code[:2]
    return CNAE_TO_SECTOR.get(prefix, "Outro")


class Company(BaseModel):
    # --- Identity ---
    cnpj: str                          # 14-digit, digits only
    razao_social: str
    nome_fantasia: str | None = None
    cnae_primary: str | None = None    # e.g. "6201-5/01"
    sector: str | None = None          # derived from CNAE
    founded_year: int | None = None
    website: str | None = None
    linkedin_url: str | None = None
    address_city: str | None = None
    address_uf: str | None = None
    is_active: bool = True

    # --- Size ---
    financials: FinancialSnapshot = Field(default_factory=FinancialSnapshot)
    size_tier: SizeTier = "Unknown"

    # --- Leadership ---
    ceo: Person | None = None
    owners: list[Owner] = Field(default_factory=list)

    # --- Public credit ---
    bndes_contracts: list[BNDESContract] = Field(default_factory=list)
    finep_contracts: list[FINEPContract] = Field(default_factory=list)

    # --- Technology ---
    tech_stack: TechStack = Field(default_factory=TechStack)

    # --- Metadata ---
    last_enriched_at: datetime = Field(default_factory=datetime.utcnow)
    enrichment_sources: list[str] = Field(default_factory=list)
    confidence_score: float = 0.0      # overall 0–1
    outreach_score: float | None = None
    outreach_notes: str | None = None

    @field_validator("cnpj", mode="before")
    @classmethod
    def normalize_cnpj(cls, v: str) -> str:
        return "".join(c for c in str(v) if c.isdigit()).zfill(14)

    def compute_size_tier(self) -> SizeTier:
        rev = self.financials.revenue_brl
        hc = self.financials.headcount
        if rev is not None:
            if rev < 50_000_000:
                return "Unknown"
            elif rev <= 850_000_000:
                return "LMM"
            elif rev <= 5_000_000_000:
                return "MM"
            else:
                return "Large"
        if hc is not None:
            if hc < 100:
                return "Unknown"
            elif hc <= 2000:
                return "LMM"
        return "Unknown"

    def derive_sector(self) -> str | None:
        if self.cnae_primary:
            return cnae_to_sector(self.cnae_primary)
        return None

    def total_bndes_value(self) -> float:
        return sum(c.value_brl for c in self.bndes_contracts)

    def total_finep_value(self) -> float:
        return sum(c.value_brl for c in self.finep_contracts if c.value_brl)

    def has_public_credit(self) -> bool:
        return bool(self.bndes_contracts or self.finep_contracts)


# ---------------------------------------------------------------------------
# Partial enrichment result (each agent returns one of these)
# ---------------------------------------------------------------------------

class PartialCompany(BaseModel):
    """Subset of Company fields returned by a single enrichment agent."""
    cnpj: str

    razao_social: str | None = None
    nome_fantasia: str | None = None
    cnae_primary: str | None = None
    sector: str | None = None
    founded_year: int | None = None
    website: str | None = None
    linkedin_url: str | None = None
    address_city: str | None = None
    address_uf: str | None = None
    is_active: bool | None = None

    financials: FinancialSnapshot | None = None
    ceo: Person | None = None
    owners: list[Owner] = Field(default_factory=list)
    bndes_contracts: list[BNDESContract] = Field(default_factory=list)
    finep_contracts: list[FINEPContract] = Field(default_factory=list)
    tech_stack: TechStack | None = None

    source: str = "unknown"
    confidence: float = 0.5


# ---------------------------------------------------------------------------
# Pipeline input
# ---------------------------------------------------------------------------

class CompanyQuery(BaseModel):
    """Input to the pipeline — at minimum a CNPJ or a company name."""
    cnpj: str | None = None
    company_name: str | None = None
    website: str | None = None

    @field_validator("cnpj", mode="before")
    @classmethod
    def normalize(cls, v: str | None) -> str | None:
        if v is None:
            return None
        digits = "".join(c for c in str(v) if c.isdigit())
        return digits.zfill(14) if digits else None
