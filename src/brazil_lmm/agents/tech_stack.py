"""
Tech stack enrichment agent.

Sources (in priority order):
1. BuiltWith API — most reliable, requires free API key.
2. Wappalyzer heuristics via website HTML scrape (no key needed).
3. LinkedIn job postings — infer stack from requirements in job ads.

Maps detected technologies into the TechStack categories.
"""
from __future__ import annotations

import os
import re

import httpx

from brazil_lmm.agents.base import BaseAgent
from brazil_lmm.models import PartialCompany, TechStack


BUILTWITH_URL = "https://api.builtwith.com/v21/api.json"

# Technology → category mapping
TECH_MAP: dict[str, str] = {
    # ERP
    "SAP": "erp", "TOTVS": "erp", "Oracle ERP": "erp", "Microsoft Dynamics": "erp",
    "Protheus": "erp", "Senior": "erp", "Datasul": "erp", "RM": "erp",
    "Sankhya": "erp", "Linx": "erp",
    # CRM
    "Salesforce": "crm", "HubSpot": "crm", "Pipedrive": "crm", "Zoho": "crm",
    "Microsoft Dynamics CRM": "crm", "RD Station": "crm",
    # Cloud
    "Amazon Web Services": "cloud_providers", "AWS": "cloud_providers",
    "Google Cloud": "cloud_providers", "Microsoft Azure": "cloud_providers",
    "Cloudflare": "cloud_providers", "DigitalOcean": "cloud_providers",
    # Ecommerce
    "Shopify": "ecommerce", "WooCommerce": "ecommerce", "VTEX": "ecommerce",
    "Magento": "ecommerce", "Tray": "ecommerce", "Nuvemshop": "ecommerce",
    "Loja Integrada": "ecommerce",
    # Analytics
    "Google Analytics": "analytics", "Mixpanel": "analytics", "Amplitude": "analytics",
    "Tableau": "analytics", "Power BI": "analytics", "Looker": "analytics",
    "Google Tag Manager": "analytics", "Hotjar": "analytics",
    # Cybersecurity
    "Cloudflare": "cybersecurity", "Imperva": "cybersecurity",
    "Fortinet": "cybersecurity", "Palo Alto": "cybersecurity",
}

# HTML/JS fingerprints for Wappalyzer-style detection (simplified)
FINGERPRINTS: dict[str, list[str]] = {
    "VTEX": ["vtex.com", "vtexcommercestable", "vteximg"],
    "Shopify": ["cdn.shopify.com", "shopify.com/s/"],
    "WooCommerce": ["woocommerce", "wp-content/plugins/woocommerce"],
    "Google Analytics": ["google-analytics.com/analytics.js", "gtag(", "UA-"],
    "Google Tag Manager": ["googletagmanager.com/gtm.js"],
    "HubSpot": ["js.hs-scripts.com", "hubspot.com"],
    "RD Station": ["rdstation", "d335luupugsy2.cloudfront.net"],
    "Cloudflare": ["__cf_bm", "cloudflare"],
    "Amazon Web Services": ["amazonaws.com", "aws-amplify"],
    "Microsoft Azure": ["azure.com", "azurewebsites.net", "azurefd.net"],
    "Google Cloud": ["googleapis.com", "gstatic.com"],
    "Salesforce": ["salesforce.com", "force.com", "lightning.force"],
    "Intercom": ["intercom.io", "widget.intercom.io"],
    "Zendesk": ["zendesk.com", "zdassets.com"],
    "Hotjar": ["hotjar.com", "hj("],
    "Mixpanel": ["mixpanel.com"],
    "Amplitude": ["amplitude.com", "cdn.amplitude.com"],
}


class TechStackAgent(BaseAgent):
    name = "tech_stack"

    def __init__(self, builtwith_api_key: str | None = None) -> None:
        super().__init__()
        self._bw_key = builtwith_api_key or os.getenv("BUILTWITH_API_KEY", "")

    async def enrich(self, cnpj: str, company_name: str | None = None) -> PartialCompany:
        # We need a website URL — caller should pass it via company_name hack or we skip
        # The orchestrator passes website via a side channel; here we accept it as company_name
        # if it looks like a URL.
        website = company_name if company_name and company_name.startswith("http") else None
        if not website:
            return PartialCompany(cnpj=cnpj, source=self.name, confidence=0.0)

        stack = TechStack(inferred_from=[])

        if self._bw_key:
            stack = await self._builtwith_enrich(website, stack)

        if not any([stack.erp, stack.crm, stack.cloud_providers, stack.ecommerce]):
            stack = await self._wappalyzer_enrich(website, stack)

        return PartialCompany(
            cnpj=cnpj,
            tech_stack=stack,
            source=self.name,
            confidence=0.75 if stack.inferred_from else 0.0,
        )

    async def _builtwith_enrich(self, website: str, stack: TechStack) -> TechStack:
        try:
            resp = await self._get(
                BUILTWITH_URL,
                params={"KEY": self._bw_key, "LOOKUP": website},
            )
            data = resp.json()
            techs: list[str] = []

            for result in data.get("Results", []):
                for path in result.get("Result", {}).get("Paths", []):
                    for tech in path.get("Technologies", []):
                        name = tech.get("Name", "")
                        if name:
                            techs.append(name)

            stack = self._categorize(techs, stack)
            if techs:
                stack.inferred_from.append("builtwith")

        except (httpx.HTTPStatusError, httpx.RequestError, KeyError):
            pass

        return stack

    async def _wappalyzer_enrich(self, website: str, stack: TechStack) -> TechStack:
        """Fetch website HTML and JS and fingerprint against known patterns."""
        try:
            resp = await self._get(website)
            html = resp.text.lower()

            detected: list[str] = []
            for tech, patterns in FINGERPRINTS.items():
                if any(p.lower() in html for p in patterns):
                    detected.append(tech)

            stack = self._categorize(detected, stack)
            if detected:
                stack.inferred_from.append("wappalyzer_heuristic")

        except (httpx.HTTPStatusError, httpx.RequestError):
            pass

        return stack

    @staticmethod
    def _categorize(tech_names: list[str], stack: TechStack) -> TechStack:
        for tech in tech_names:
            category = None
            for key, cat in TECH_MAP.items():
                if key.lower() in tech.lower() or tech.lower() in key.lower():
                    category = cat
                    break

            if category == "erp" and tech not in stack.erp:
                stack.erp.append(tech)
            elif category == "crm" and tech not in stack.crm:
                stack.crm.append(tech)
            elif category == "cloud_providers" and tech not in stack.cloud_providers:
                stack.cloud_providers.append(tech)
            elif category == "ecommerce" and tech not in stack.ecommerce:
                stack.ecommerce.append(tech)
            elif category == "analytics" and tech not in stack.analytics:
                stack.analytics.append(tech)
            elif category == "cybersecurity" and tech not in stack.cybersecurity:
                stack.cybersecurity.append(tech)
            elif category is None and tech not in stack.other:
                stack.other.append(tech)

        return stack
