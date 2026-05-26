"""
BNDES Upload Discovery — parses a manually downloaded BNDES Excel/CSV file.

The user downloads the file from:
  bndes.gov.br → Transparência → Consulta Operações → Exportar

This approach is 100% reliable since it has no external API dependencies.
Supports both .xlsx and .csv formats.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from brazil_lmm.discovery.bndes_discovery import DiscoveredCompany
from brazil_lmm.discovery.sector_map import INDUSTRY_CNAES, HEALTH_CNAES


MIN_CONTRACT_BRL = 500_000
MAX_CONTRACT_BRL = 80_000_000

# All known BNDES CSV/Excel column name variants
CNPJ_COLS    = ["cnpj do beneficiário final", "cnpj", "cnpj beneficiario", "cpf/cnpj"]
NAME_COLS    = ["beneficiário final", "razão social", "nome", "beneficiario final", "cliente"]
VALUE_COLS   = ["valor contratado (r$)", "valor contratado", "valor (r$)", "valor"]
CNAE_COLS    = ["cnae", "setor cnae", "setor", "atividade"]
UF_COLS      = ["uf", "estado"]
CITY_COLS    = ["município do beneficiário final", "municipio", "município", "cidade"]
DATE_COLS    = ["data de contratação", "data contratacao", "data", "ano"]


def _find_col(headers: list[str], candidates: list[str]) -> str | None:
    lower = [h.lower().strip() for h in headers]
    for c in candidates:
        if c in lower:
            return headers[lower.index(c)]
    return None


def parse_bndes_file(
    content: bytes,
    filename: str,
    sectors: list[str],
    ufs: list[str] | None = None,
) -> list[DiscoveredCompany]:
    """Parse uploaded BNDES file (CSV or XLSX) and return matched companies."""

    allowed_cnaes = _sector_cnaes(sectors)

    if filename.lower().endswith(".xlsx") or filename.lower().endswith(".xls"):
        rows, headers = _parse_excel(content)
    else:
        rows, headers = _parse_csv(content)

    print(f"[UPLOAD] Headers found: {headers}")
    print(f"[UPLOAD] Total rows: {len(rows)}")

    # Map column names
    cnpj_col  = _find_col(headers, CNPJ_COLS)
    name_col  = _find_col(headers, NAME_COLS)
    value_col = _find_col(headers, VALUE_COLS)
    cnae_col  = _find_col(headers, CNAE_COLS)
    uf_col    = _find_col(headers, UF_COLS)
    city_col  = _find_col(headers, CITY_COLS)
    date_col  = _find_col(headers, DATE_COLS)

    print(f"[UPLOAD] Mapped: cnpj={cnpj_col} name={name_col} value={value_col} cnae={cnae_col}")

    if not cnpj_col:
        raise ValueError(f"Could not find CNPJ column. Available columns: {headers}")

    aggregated: dict[str, DiscoveredCompany] = {}

    for row in rows:
        cnpj = re.sub(r"\D", "", str(row.get(cnpj_col, "") or ""))
        if len(cnpj) != 14:
            continue

        uf = (str(row.get(uf_col, "") or "") if uf_col else "").strip().upper()
        if ufs and uf and uf not in [u.upper() for u in ufs]:
            continue

        cnae_raw = str(row.get(cnae_col, "") or "") if cnae_col else ""
        cnae_prefix = re.sub(r"\D", "", cnae_raw)[:2]
        if allowed_cnaes and cnae_prefix and cnae_prefix not in allowed_cnaes:
            continue

        value = 0.0
        if value_col:
            raw_val = str(row.get(value_col, "") or "0")
            raw_val = raw_val.replace("R$", "").replace(" ", "")
            # Handle Brazilian number format (1.234.567,89)
            if "," in raw_val and "." in raw_val:
                raw_val = raw_val.replace(".", "").replace(",", ".")
            elif "," in raw_val:
                raw_val = raw_val.replace(",", ".")
            try:
                value = float(raw_val)
            except ValueError:
                value = 0.0

        if value > 0 and (value < MIN_CONTRACT_BRL or value > MAX_CONTRACT_BRL):
            continue

        razao = str(row.get(name_col, "") or "") if name_col else ""
        city  = str(row.get(city_col, "") or "").title() if city_col else ""
        date  = str(row.get(date_col, "") or "") if date_col else ""
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

    print(f"[UPLOAD] Matched: {len(aggregated)} companies after filters")

    # Rank
    companies = list(aggregated.values())
    for c in companies:
        score = min(c.contract_count / 10, 0.3)
        score += min(c.total_bndes_brl / 80_000_000, 0.4)
        score += 0.3 if (c.latest_year and c.latest_year >= 2020) else 0.0
        c.score_hint = round(score, 3)

    return sorted(companies, key=lambda x: x.score_hint, reverse=True)


def _parse_csv(content: bytes) -> tuple[list[dict], list[str]]:
    text = None
    for enc in ["utf-8-sig", "latin-1", "utf-8", "cp1252"]:
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        raise ValueError("Could not decode CSV file")

    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # Try semicolon first (Brazilian standard), then comma
    for delimiter in [";", ","]:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)
        if rows and len(rows[0]) > 2:
            return rows, list(rows[0].keys()) if rows else []

    return [], []


def _parse_excel(content: bytes) -> tuple[list[dict], list[str]]:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows_iter = iter(ws.rows)
        headers = [str(cell.value or "").strip() for cell in next(rows_iter)]
        rows = []
        for row in rows_iter:
            values = [str(cell.value or "").strip() for cell in row]
            if any(values):
                rows.append(dict(zip(headers, values)))
        return rows, headers
    except ImportError:
        raise ValueError("Install openpyxl to parse Excel files: pip install openpyxl")


def _sector_cnaes(sectors: list[str]) -> set[str]:
    result: set[str] = set()
    for s in sectors:
        if "industria" in s.lower() or "indústria" in s.lower():
            result |= INDUSTRY_CNAES
        if "saude" in s.lower() or "saúde" in s.lower():
            result |= HEALTH_CNAES
    return result
