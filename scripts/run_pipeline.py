#!/usr/bin/env python3
"""
CLI entrypoint for the Brazil LMM company intelligence pipeline.

Usage examples:
  # Single CNPJ
  python scripts/run_pipeline.py --cnpj 12345678000195

  # Batch from CSV file (columns: cnpj, company_name, website)
  python scripts/run_pipeline.py --input companies.csv

  # Output to Google Sheets
  python scripts/run_pipeline.py --input companies.csv --sheets

  # Filter by sector and UF, export top 50 by outreach score
  python scripts/run_pipeline.py --input companies.csv --sector "TI e Software" --uf SP --limit 50
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from brazil_lmm.export.exporter import export_csv, export_google_sheets
from brazil_lmm.models import CompanyQuery
from brazil_lmm.orchestrator import Orchestrator
from brazil_lmm.storage.database import Database

console = Console()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Brazil LMM Company Intelligence Pipeline")
    p.add_argument("--cnpj", help="Single CNPJ to enrich")
    p.add_argument("--name", help="Company name (used with --cnpj or standalone search)")
    p.add_argument("--input", help="Path to CSV with columns: cnpj, company_name, website")
    p.add_argument("--output", default="output/companies.csv", help="Output CSV path")
    p.add_argument("--sheets", action="store_true", help="Also export to Google Sheets")
    p.add_argument("--sector", help="Filter results by sector")
    p.add_argument("--uf", help="Filter results by UF (e.g. SP, RJ)")
    p.add_argument("--min-score", type=float, default=0.0, help="Minimum outreach score (0–1)")
    p.add_argument("--limit", type=int, default=500, help="Max companies to process")
    p.add_argument("--no-db", action="store_true", help="Skip database storage")
    p.add_argument("--from-db", action="store_true", help="Export from DB instead of scraping")
    return p.parse_args()


def load_queries(args: argparse.Namespace) -> list[CompanyQuery]:
    queries: list[CompanyQuery] = []

    if args.cnpj:
        queries.append(CompanyQuery(cnpj=args.cnpj, company_name=args.name))
        return queries

    if args.input:
        path = Path(args.input)
        with path.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if i >= args.limit:
                    break
                queries.append(CompanyQuery(
                    cnpj=row.get("cnpj") or row.get("CNPJ"),
                    company_name=row.get("company_name") or row.get("razao_social") or row.get("nome"),
                    website=row.get("website"),
                ))
        return queries

    console.print("[red]Provide --cnpj or --input[/red]")
    sys.exit(1)


async def main() -> None:
    args = parse_args()

    orchestrator = Orchestrator()
    db: Database | None = None

    if not args.no_db and os.getenv("DATABASE_URL"):
        db = Database()
        await db.init()

    # Export from DB (no scraping)
    if args.from_db and db:
        companies = await db.list_lmm(
            sector=args.sector,
            uf=args.uf,
            min_score=args.min_score,
            limit=args.limit,
        )
        _export_and_display(companies, args)
        if db:
            await db.close()
        return

    queries = load_queries(args)
    companies = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Enriching {len(queries)} companies...", total=len(queries))

        for i in range(0, len(queries), 10):
            batch = queries[i : i + 10]
            batch_results = await orchestrator.process_batch(batch)
            companies.extend(batch_results)

            if db:
                await db.upsert_batch(batch_results)

            progress.advance(task, len(batch))

    _export_and_display(companies, args)

    if db:
        await db.close()


def _export_and_display(companies: list, args: argparse.Namespace) -> None:
    # Apply filters for DB exports (already filtered for DB path)
    if args.sector:
        companies = [c for c in companies if c.sector == args.sector]
    if args.uf:
        companies = [c for c in companies if c.address_uf == args.uf]
    if args.min_score:
        companies = [c for c in companies if (c.outreach_score or 0) >= args.min_score]

    companies.sort(key=lambda c: c.outreach_score or 0, reverse=True)

    # CSV export
    out_path = export_csv(companies, args.output)
    console.print(f"\n[green]CSV saved to:[/green] {out_path}")

    # Google Sheets
    if args.sheets:
        url = export_google_sheets(companies)
        console.print(f"[green]Google Sheets:[/green] {url}")

    # Summary table
    table = Table(title=f"Top 10 by Outreach Score ({len(companies)} total)")
    table.add_column("Razão Social", style="cyan", max_width=35)
    table.add_column("Setor", max_width=20)
    table.add_column("UF")
    table.add_column("Score", justify="right")
    table.add_column("BNDES", justify="center")
    table.add_column("FINEP", justify="center")
    table.add_column("CEO", max_width=25)

    for c in companies[:10]:
        table.add_row(
            c.razao_social[:35],
            (c.sector or "")[:20],
            c.address_uf or "",
            f"{c.outreach_score:.0%}" if c.outreach_score else "—",
            "✓" if c.bndes_contracts else "—",
            "✓" if c.finep_contracts else "—",
            c.ceo.full_name[:25] if c.ceo else "—",
        )

    console.print(table)


if __name__ == "__main__":
    asyncio.run(main())
