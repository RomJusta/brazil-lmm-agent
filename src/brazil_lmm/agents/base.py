from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from brazil_lmm.models import PartialCompany


class BaseAgent(ABC):
    """Base class for all enrichment agents."""

    name: str = "base"
    RATE_LIMIT = float(os.getenv("REQUESTS_PER_SECOND", "2"))

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(int(self.RATE_LIMIT))
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "BaseAgent":
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BrazilLMMAgent/0.1)"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Agent must be used as async context manager")
        return self._client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def _get(self, url: str, **kwargs: object) -> httpx.Response:
        async with self._semaphore:
            resp = await self.client.get(url, **kwargs)  # type: ignore[arg-type]
            resp.raise_for_status()
            await asyncio.sleep(1.0 / self.RATE_LIMIT)
            return resp

    @abstractmethod
    async def enrich(self, cnpj: str, company_name: str | None = None) -> PartialCompany:
        """Return a PartialCompany with whichever fields this agent can fill."""
        ...
