"""
PostgreSQL storage layer using SQLAlchemy async + asyncpg.

Schema uses a single JSONB column for the full Company record alongside
indexed scalar columns for fast querying (CNPJ, sector, size_tier, outreach_score).
This avoids a complex relational schema while keeping full flexibility.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, String, Text, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, mapped_column

from brazil_lmm.models import Company


class Base(DeclarativeBase):
    pass


class CompanyRecord(Base):
    __tablename__ = "companies"

    cnpj = Column(String(14), primary_key=True)
    razao_social = Column(String(500), nullable=False, index=True)
    sector = Column(String(200), index=True, nullable=True)
    size_tier = Column(String(20), index=True, nullable=True)
    address_uf = Column(String(2), index=True, nullable=True)
    outreach_score = Column(Float, index=True, nullable=True)
    has_bndes = Column(String(1), default="N")   # Y/N
    has_finep = Column(String(1), default="N")
    is_active = Column(String(1), default="Y")
    last_enriched_at = Column(DateTime, nullable=True)
    data = Column(JSONB, nullable=False)         # full Company JSON

    __table_args__ = (
        Index("ix_companies_outreach_score", "outreach_score"),
        Index("ix_companies_sector_uf", "sector", "address_uf"),
    )


class Database:
    def __init__(self, database_url: str | None = None) -> None:
        url = database_url or os.environ["DATABASE_URL"]
        self._engine = create_async_engine(url, pool_size=5, max_overflow=10)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def upsert(self, company: Company) -> None:
        record = CompanyRecord(
            cnpj=company.cnpj,
            razao_social=company.razao_social,
            sector=company.sector,
            size_tier=company.size_tier,
            address_uf=company.address_uf,
            outreach_score=company.outreach_score,
            has_bndes="Y" if company.bndes_contracts else "N",
            has_finep="Y" if company.finep_contracts else "N",
            is_active="Y" if company.is_active else "N",
            last_enriched_at=company.last_enriched_at,
            data=json.loads(company.model_dump_json()),
        )
        async with self._session_factory() as session:
            await session.merge(record)
            await session.commit()

    async def upsert_batch(self, companies: list[Company]) -> None:
        async with self._session_factory() as session:
            for company in companies:
                record = CompanyRecord(
                    cnpj=company.cnpj,
                    razao_social=company.razao_social,
                    sector=company.sector,
                    size_tier=company.size_tier,
                    address_uf=company.address_uf,
                    outreach_score=company.outreach_score,
                    has_bndes="Y" if company.bndes_contracts else "N",
                    has_finep="Y" if company.finep_contracts else "N",
                    is_active="Y" if company.is_active else "N",
                    last_enriched_at=company.last_enriched_at,
                    data=json.loads(company.model_dump_json()),
                )
                await session.merge(record)
            await session.commit()

    async def get(self, cnpj: str) -> Company | None:
        async with self._session_factory() as session:
            result = await session.get(CompanyRecord, cnpj)
            if result is None:
                return None
            return Company(**result.data)

    async def list_lmm(
        self,
        *,
        sector: str | None = None,
        uf: str | None = None,
        min_score: float = 0.0,
        limit: int = 100,
    ) -> list[Company]:
        async with self._session_factory() as session:
            q = select(CompanyRecord).where(
                CompanyRecord.size_tier == "LMM",
                CompanyRecord.is_active == "Y",
                CompanyRecord.outreach_score >= min_score,
            )
            if sector:
                q = q.where(CompanyRecord.sector == sector)
            if uf:
                q = q.where(CompanyRecord.address_uf == uf)
            q = q.order_by(CompanyRecord.outreach_score.desc()).limit(limit)  # type: ignore[attr-defined]

            rows = await session.execute(q)
            return [Company(**r.data) for r in rows.scalars()]

    async def close(self) -> None:
        await self._engine.dispose()
