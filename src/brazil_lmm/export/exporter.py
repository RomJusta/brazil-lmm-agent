"""
Export layer — CSV and Google Sheets output for the commercial team.

Each company is flattened into a single row for CRM ingestion.
Columns are ordered by priority for a sales analyst reading left-to-right.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from brazil_lmm.models import Company


COLUMNS = [
    # Identity
    "cnpj",
    "razao_social",
    "nome_fantasia",
    "sector",
    "size_tier",
    "address_city",
    "address_uf",
    "founded_year",
    "website",
    "linkedin_url",
    "is_active",
    # Leadership
    "ceo_name",
    "ceo_role",
    "ceo_linkedin",
    "owners",
    # Financials
    "revenue_brl",
    "ebitda_brl",
    "ebitda_margin_pct",
    "headcount",
    "financial_source",
    "financial_year",
    # Public credit
    "bndes_total_brl",
    "bndes_contracts_count",
    "bndes_products",
    "finep_total_brl",
    "finep_contracts_count",
    "finep_programs",
    "finep_modalities",
    # Technology
    "tech_erp",
    "tech_crm",
    "tech_cloud",
    "tech_ecommerce",
    "tech_analytics",
    "tech_other",
    "tech_sources",
    # Scoring
    "outreach_score",
    "confidence_score",
    "outreach_notes",
    "last_enriched_at",
    "enrichment_sources",
]


def _flatten(company: Company) -> dict:
    owners_str = "; ".join(
        f"{o.name} ({o.ownership_pct or '?'}%)" for o in company.owners
    )
    bndes_products = list({c.product for c in company.bndes_contracts})
    finep_programs = list({c.program for c in company.finep_contracts})
    finep_modalities = list({c.modality for c in company.finep_contracts})

    ebitda_margin = company.financials.ebitda_margin
    return {
        "cnpj": company.cnpj,
        "razao_social": company.razao_social,
        "nome_fantasia": company.nome_fantasia or "",
        "sector": company.sector or "",
        "size_tier": company.size_tier,
        "address_city": company.address_city or "",
        "address_uf": company.address_uf or "",
        "founded_year": company.founded_year or "",
        "website": company.website or "",
        "linkedin_url": company.linkedin_url or "",
        "is_active": "Sim" if company.is_active else "Não",

        "ceo_name": company.ceo.full_name if company.ceo else "",
        "ceo_role": company.ceo.role if company.ceo else "",
        "ceo_linkedin": company.ceo.linkedin_url if company.ceo else "",
        "owners": owners_str,

        "revenue_brl": company.financials.revenue_brl or "",
        "ebitda_brl": company.financials.ebitda_brl or "",
        "ebitda_margin_pct": f"{ebitda_margin:.1%}" if ebitda_margin else "",
        "headcount": company.financials.headcount or "",
        "financial_source": company.financials.source or "",
        "financial_year": company.financials.reference_year or "",

        "bndes_total_brl": company.total_bndes_value() or "",
        "bndes_contracts_count": len(company.bndes_contracts),
        "bndes_products": "; ".join(bndes_products),
        "finep_total_brl": company.total_finep_value() or "",
        "finep_contracts_count": len(company.finep_contracts),
        "finep_programs": "; ".join(finep_programs),
        "finep_modalities": "; ".join(finep_modalities),

        "tech_erp": "; ".join(company.tech_stack.erp),
        "tech_crm": "; ".join(company.tech_stack.crm),
        "tech_cloud": "; ".join(company.tech_stack.cloud_providers),
        "tech_ecommerce": "; ".join(company.tech_stack.ecommerce),
        "tech_analytics": "; ".join(company.tech_stack.analytics),
        "tech_other": "; ".join(company.tech_stack.other),
        "tech_sources": "; ".join(company.tech_stack.inferred_from),

        "outreach_score": company.outreach_score or "",
        "confidence_score": round(company.confidence_score, 2),
        "outreach_notes": company.outreach_notes or "",
        "last_enriched_at": company.last_enriched_at.strftime("%Y-%m-%d %H:%M"),
        "enrichment_sources": "; ".join(company.enrichment_sources),
    }


def export_csv(companies: list[Company], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig for Excel compatibility
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for company in companies:
            writer.writerow(_flatten(company))

    return path


def export_google_sheets(
    companies: list[Company],
    sheet_id: str | None = None,
    worksheet_name: str = "LMM Companies",
    service_account_json: str | None = None,
) -> str:
    """
    Writes to a Google Sheet. Returns the sheet URL.

    Requires:
    - GOOGLE_SERVICE_ACCOUNT_JSON env var (path to JSON key file)
    - GOOGLE_SHEET_ID env var (the spreadsheet ID)
    - The service account must have Editor access to the sheet.
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as e:
        raise ImportError("Install gspread and google-auth: pip install gspread google-auth") from e

    sa_path = service_account_json or os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    sid = sheet_id or os.environ["GOOGLE_SHEET_ID"]

    creds = Credentials.from_service_account_file(
        sa_path,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(sid)

    try:
        ws = spreadsheet.worksheet(worksheet_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=worksheet_name, rows=5000, cols=len(COLUMNS))

    rows = [COLUMNS]  # header
    for company in companies:
        flat = _flatten(company)
        rows.append([str(flat.get(col, "")) for col in COLUMNS])

    ws.update(rows, value_input_option="USER_ENTERED")

    # Bold header row
    ws.format("1:1", {"textFormat": {"bold": True}})

    return f"https://docs.google.com/spreadsheets/d/{sid}"
