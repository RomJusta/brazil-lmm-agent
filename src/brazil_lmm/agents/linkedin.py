"""
LinkedIn enrichment agent.

LinkedIn does not have a public API for company data. This agent uses
Playwright to scrape the company's public LinkedIn page using a session
cookie (li_at), which is the most reliable approach without a paid API.

Data extracted:
- CEO / C-level executives from "People" tab
- Company website (cross-reference)
- Headcount (LinkedIn-reported)
- Recent job postings (for tech stack inference)

IMPORTANT: Use responsibly. LinkedIn's ToS restricts automated scraping.
This agent includes conservative rate limiting (5s between requests).
Set LINKEDIN_LI_AT_COOKIE in your .env before use.
"""
from __future__ import annotations

import asyncio
import json
import os
import re

from brazil_lmm.agents.base import BaseAgent
from brazil_lmm.models import FinancialSnapshot, PartialCompany, Person, TechStack


LINKEDIN_SEARCH_URL = "https://www.linkedin.com/search/results/companies/?keywords={name}"
LINKEDIN_COMPANY_URL = "https://www.linkedin.com/company/{slug}"
LINKEDIN_PEOPLE_URL = "https://www.linkedin.com/company/{slug}/people/?keywords=CEO"

TECH_KEYWORDS_IN_JOBS = [
    "SAP", "TOTVS", "Oracle", "Salesforce", "HubSpot", "AWS", "Azure",
    "Google Cloud", "Kubernetes", "Python", "Java", ".NET", "Protheus",
    "Sankhya", "Linx", "VTEX", "Shopify", "Power BI", "Tableau",
]


class LinkedInAgent(BaseAgent):
    name = "linkedin"

    def __init__(self, li_at_cookie: str | None = None) -> None:
        super().__init__()
        self._li_at = li_at_cookie or os.getenv("LINKEDIN_LI_AT_COOKIE", "")

    async def enrich(self, cnpj: str, company_name: str | None = None) -> PartialCompany:
        if not self._li_at or not company_name:
            return PartialCompany(cnpj=cnpj, source=self.name, confidence=0.0)

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                ctx = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                )
                await ctx.add_cookies([{
                    "name": "li_at",
                    "value": self._li_at,
                    "domain": ".linkedin.com",
                    "path": "/",
                }])

                page = await ctx.new_page()
                result = await self._scrape_company(page, company_name)
                await browser.close()
                return PartialCompany(cnpj=cnpj, source=self.name, **result)

        except ImportError:
            return PartialCompany(
                cnpj=cnpj, source=self.name, confidence=0.0
            )
        except Exception:
            return PartialCompany(cnpj=cnpj, source=self.name, confidence=0.0)

    async def _scrape_company(self, page: object, company_name: str) -> dict:
        from playwright.async_api import Page
        page: Page

        # Step 1: Search for company
        search_url = LINKEDIN_SEARCH_URL.format(name=company_name.replace(" ", "%20"))
        await page.goto(search_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Extract first company result slug
        slug = await self._extract_first_slug(page)
        if not slug:
            return {"confidence": 0.0}

        await asyncio.sleep(2)

        # Step 2: Visit company page
        company_url = LINKEDIN_COMPANY_URL.format(slug=slug)
        await page.goto(company_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        headcount = await self._extract_headcount(page)
        website = await self._extract_website(page)

        # Step 3: People tab — look for CEO
        await asyncio.sleep(2)
        people_url = LINKEDIN_PEOPLE_URL.format(slug=slug)
        await page.goto(people_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        ceo = await self._extract_ceo(page, slug)

        # Step 4: Jobs tab for tech stack hints
        jobs_url = f"https://www.linkedin.com/company/{slug}/jobs/"
        await page.goto(jobs_url, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        job_text = await page.inner_text("body")
        tech_from_jobs = self._extract_tech_from_jobs(job_text)

        result: dict = {
            "linkedin_url": company_url,
            "confidence": 0.7,
        }
        if headcount:
            result["financials"] = FinancialSnapshot(headcount=headcount, source="linkedin")
        if website:
            result["website"] = website
        if ceo:
            result["ceo"] = ceo
        if tech_from_jobs:
            result["tech_stack"] = TechStack(
                other=tech_from_jobs,
                inferred_from=["linkedin_jobs"],
            )

        return result

    async def _extract_first_slug(self, page: object) -> str | None:
        from playwright.async_api import Page
        page: Page
        try:
            links = await page.query_selector_all("a[href*='/company/']")
            for link in links:
                href = await link.get_attribute("href")
                if href:
                    m = re.search(r"/company/([^/?]+)", href)
                    if m:
                        return m.group(1)
        except Exception:
            pass
        return None

    async def _extract_headcount(self, page: object) -> int | None:
        from playwright.async_api import Page
        page: Page
        try:
            text = await page.inner_text("body")
            # LinkedIn shows "1,234 employees" or "500-1,000 employees"
            m = re.search(r"([\d,]+)\s+(?:employees|funcionários)", text, re.IGNORECASE)
            if m:
                return int(m.group(1).replace(",", ""))
            # Range format
            m = re.search(r"(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)\s+(?:employees|funcionários)", text, re.IGNORECASE)
            if m:
                low = int(m.group(1).replace(",", ""))
                high = int(m.group(2).replace(",", ""))
                return (low + high) // 2
        except Exception:
            pass
        return None

    async def _extract_website(self, page: object) -> str | None:
        from playwright.async_api import Page
        page: Page
        try:
            el = await page.query_selector("a[data-field='website']")
            if el:
                return await el.get_attribute("href")
            # Fallback: look for external link in about section
            text = await page.inner_text("body")
            m = re.search(r"https?://(?!linkedin\.com)[^\s\"'<>]+", text)
            if m:
                return m.group(0).rstrip("/.,;)")
        except Exception:
            pass
        return None

    async def _extract_ceo(self, page: object, slug: str) -> Person | None:
        from playwright.async_api import Page
        page: Page
        try:
            text = await page.inner_text("body")
            # Look for pattern: "Name\nCEO" or "CEO at Company"
            patterns = [
                r"([A-ZÀ-Ú][a-zà-ú]+(?: [A-ZÀ-Ú][a-zà-ú]+)+)\nCEO",
                r"CEO\s+at\s+\w.+\n([A-ZÀ-Ú][a-zà-ú]+(?: [A-ZÀ-Ú][a-zà-ú]+)+)",
            ]
            for pattern in patterns:
                m = re.search(pattern, text)
                if m:
                    name = m.group(1).strip()
                    profile_links = await page.query_selector_all(f"a[href*='/in/']")
                    linkedin_url = None
                    for link in profile_links:
                        link_text = await link.inner_text()
                        if name.split()[0] in link_text:
                            linkedin_url = await link.get_attribute("href")
                            break
                    return Person(
                        full_name=name,
                        role="CEO",
                        linkedin_url=linkedin_url,
                        source="linkedin",
                    )
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_tech_from_jobs(text: str) -> list[str]:
        found: list[str] = []
        for kw in TECH_KEYWORDS_IN_JOBS:
            if kw.lower() in text.lower() and kw not in found:
                found.append(kw)
        return found
