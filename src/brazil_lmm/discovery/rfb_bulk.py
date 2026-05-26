"""
Receita Federal Bulk Discovery — finds active companies by CNAE from the
official CNPJ open data published by the Receita Federal do Brasil.

Data source: dados.gov.br / Receita Federal open data
URL: https://dados.rfb.gov.br/CNPJ/dados_abertos_cnpj/

The dataset is published as monthly ZIP files containing CSVs with all
active CNPJs. We use the "Estabelecimentos" file which has CNPJ, CNAE,
UF, municipality, and status.

Because the full file is large (~2GB), we stream and filter in-memory
without loading everything. We cap at `limit` results per run.
"""
from __future__ import annotations

import asyncio
import csv
import io
import re
import zipfile
from dataclasses import dataclass

import httpx

from brazil_lmm.discovery.sector_map import ALL_TARGET_CNAES, INDUSTRY_CNAES, HEALTH_CNAES


# Receita Federal open data index
RFB_INDEX_URL = "https://dados.rfb.gov.br/CNPJ/dados_abertos_cnpj/2024-11/"
RFB_ESTABELECIMENTOS_URL = RFB_INDEX_URL + "Estabelecimentos0.zip"


@dataclass
class RFBCandidate:
    cnpj: str
    razao_social: str
    cnae: str
    uf: str
    city: str
    discovery_source: str = "rfb_bulk"


class RFBBulkDiscovery:
    """
    Streams the Receita Federal bulk CNPJ file and returns candidates
    matching sector and UF filters.

    Note: This download is large (~300MB compressed). It runs once and
    the results are cached in memory for the session.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=300.0, follow_redirects=True)
        self._cache: list[RFBCandidate] | None = None

    async def discover(
        self,
        sectors: list[str],
        ufs: list[str] | None = None,
        limit: int = 1000,
    ) -> list[RFBCandidate]:
        allowed_cnaes = self._sector_cnaes(sectors)

        if self._cache is None:
            self._cache = await self._stream_and_filter(allowed_cnaes, ufs, limit * 3)

        results = self._cache
        if ufs:
            uf_set = {u.upper() for u in ufs}
            results = [r for r in results if r.uf in uf_set]
        if allowed_cnaes:
            results = [r for r in results if r.cnae[:2] in allowed_cnaes]

        return results[:limit]

    async def _stream_and_filter(
        self,
        allowed_cnaes: set[str],
        ufs: list[str] | None,
        limit: int,
    ) -> list[RFBCandidate]:
        candidates: list[RFBCandidate] = []
        uf_set = {u.upper() for u in ufs} if ufs else None

        try:
            resp = await self._client.get(RFB_ESTABELECIMENTOS_URL)
            resp.raise_for_status()
            raw = resp.content

            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                filename = zf.namelist()[0]
                with zf.open(filename) as f:
                    # RFB CSVs are latin-1, semicolon-delimited, no header
                    # Column order (Estabelecimentos):
                    # 0=cnpj_basico, 1=cnpj_ordem, 2=cnpj_dv, 3=id_matriz_filial,
                    # 4=nome_fantasia, 5=situacao_cadastral, 6=data_situacao,
                    # 7=motivo_situacao, 8=nm_cidade_exterior, 9=pais,
                    # 10=data_inicio, 11=cnae_principal, 12=cnae_secundario,
                    # 13=tipo_logradouro, 14=logradouro, 15=numero, 16=complemento,
                    # 17=bairro, 18=cep, 19=uf, 20=municipio, ...
                    text = f.read().decode("latin-1")
                    reader = csv.reader(io.StringIO(text), delimiter=";")

                    for row in reader:
                        if len(row) < 21:
                            continue

                        situacao = row[5].strip()
                        if situacao != "02":  # 02 = ATIVA
                            continue

                        cnae = re.sub(r"\D", "", row[11].strip())
                        if not cnae:
                            continue
                        if allowed_cnaes and cnae[:2] not in allowed_cnaes:
                            continue

                        uf = row[19].strip().upper()
                        if uf_set and uf not in uf_set:
                            continue

                        cnpj = row[0].strip() + row[1].strip() + row[2].strip()
                        cnpj = re.sub(r"\D", "", cnpj).zfill(14)
                        nome = row[4].strip() or ""
                        city = row[20].strip().title()

                        candidates.append(RFBCandidate(
                            cnpj=cnpj,
                            razao_social=nome,
                            cnae=cnae,
                            uf=uf,
                            city=city,
                        ))

                        if len(candidates) >= limit:
                            break

        except (httpx.HTTPStatusError, httpx.RequestError, zipfile.BadZipFile, Exception):
            # RFB bulk download can fail — fall back gracefully
            pass

        return candidates

    @staticmethod
    def _sector_cnaes(sectors: list[str]) -> set[str]:
        result: set[str] = set()
        for s in sectors:
            if "industria" in s.lower() or "indústria" in s.lower():
                result |= INDUSTRY_CNAES
            if "saude" in s.lower() or "saúde" in s.lower():
                result |= HEALTH_CNAES
        return result

    async def close(self) -> None:
        await self._client.aclose()
