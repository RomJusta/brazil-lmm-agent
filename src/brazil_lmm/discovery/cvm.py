"""
CVM (Comissão de Valores Mobiliários) Discovery — dados.cvm.gov.br

Completamente gratuito, sem autenticação.

Fontes usadas:
  1. Cadastro de Cias Abertas — lista todas empresas registradas na CVM com CNPJ e setor
  2. DFP (Demonstrações Financeiras Padronizadas) — receita líquida anual real (conta 3.01)

Fluxo:
  - Baixa o cadastro CSV (~500 KB)
  - Baixa o DFP do último ano disponível (~zip com CSVs de DRE)
  - Filtra por receita R$50M–R$850M e setor relevante
  - Retorna DiscoveredCompany com receita verificada e score_hint alto

Limitação: cobre apenas empresas listadas na B3 (~500 cias abertas ativas).
Para empresas privadas, usar Econodata + seed list.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime

import httpx

from brazil_lmm.discovery.bndes_discovery import DiscoveredCompany
from brazil_lmm.discovery.sector_map import INDUSTRY_CNAES, HEALTH_CNAES


# CVM open data endpoints — sem autenticação
CVM_CAD_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"
CVM_DFP_BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/"

# Receita líquida = conta 3.01 no padrão XBRL da CVM
RECEITA_LIQUIDA_CODE = "3.01"

# Faixa LMM em R$
LMM_MIN = 50_000_000
LMM_MAX = 850_000_000

# Setores CVM que mapeiam para Indústria + Saúde
INDUSTRY_SETOR_KEYWORDS = [
    "industrial", "industria", "manufat", "aliment", "bebida", "quimic",
    "farmac", "plastico", "borracha", "madeira", "papel", "celulose",
    "textil", "vestuario", "calcado", "metalurg", "siderurg", "mineracao",
    "construcao", "cimento", "ceramica", "vidro", "moveis", "eletronico",
    "maquinas", "equipamentos", "automovel", "autopeça", "agricol",
    "agropecuar", "fertilizante", "embalagem",
]
HEALTH_SETOR_KEYWORDS = [
    "saude", "hospital", "farmaceutic", "medicament", "diagnostico",
    "laboratorio", "clinica", "medic", "biotech", "odonto",
]


class CVMDiscovery:
    """Descobre empresas listadas na B3 com receita verificada na faixa LMM."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=60.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BrazilLMMAgent/0.1)"},
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def discover(
        self,
        sectors: list[str],
        ufs: list[str] | None = None,
        limit: int = 200,
    ) -> list[DiscoveredCompany]:
        want_industria = any("industria" in s.lower() or "indústria" in s.lower() for s in sectors)
        want_saude = any("saude" in s.lower() or "saúde" in s.lower() for s in sectors)

        try:
            # 1. Cadastro de todas as cias abertas
            cadastro = await self._fetch_cadastro()
            print(f"[CVM] {len(cadastro)} empresas no cadastro CVM")

            # 2. Receita do último DFP disponível
            receita_map = await self._fetch_receita()
            print(f"[CVM] Receita disponível para {len(receita_map)} empresas")

            # 3. Filtrar por receita LMM e setor
            results: list[DiscoveredCompany] = []
            for cnpj_clean, info in cadastro.items():
                setor_lower = info.get("setor", "").lower()
                situacao = info.get("situacao", "").upper()

                # Só ativas
                if "ATIVO" not in situacao and "FASE" not in situacao:
                    continue

                # Filtro de setor
                is_industria = any(k in setor_lower for k in INDUSTRY_SETOR_KEYWORDS)
                is_saude = any(k in setor_lower for k in HEALTH_SETOR_KEYWORDS)
                if not (
                    (want_industria and is_industria)
                    or (want_saude and is_saude)
                ):
                    continue

                # Filtro de UF
                uf = info.get("uf", "")
                if ufs and uf not in [u.upper() for u in ufs]:
                    continue

                # Receita — se temos dado CVM, filtrar pela faixa
                receita = receita_map.get(cnpj_clean)
                if receita is not None:
                    if receita < LMM_MIN or receita > LMM_MAX:
                        continue
                    score_hint = 0.7  # receita verificada
                else:
                    score_hint = 0.35  # listada na B3 mas sem DFP recente

                sector_label = "Saúde" if is_saude else "Indústria"

                results.append(DiscoveredCompany(
                    cnpj=cnpj_clean,
                    razao_social=info.get("nome", ""),
                    sector_hint=sector_label,
                    uf=uf,
                    city=info.get("municipio", ""),
                    total_bndes_brl=0.0,
                    contract_count=0,
                    latest_year=None,
                    discovery_source="cvm",
                    score_hint=score_hint,
                    revenue_hint=receita,
                ))

            # Ordenar: receita verificada primeiro, depois score
            results.sort(key=lambda x: (x.score_hint, x.revenue_hint or 0), reverse=True)
            results = results[:limit]
            print(f"[CVM] {len(results)} empresas na faixa LMM encontradas")
            return results

        except Exception as e:
            print(f"[CVM] Erro: {e}")
            return []

    async def _fetch_cadastro(self) -> dict[str, dict]:
        """Baixa o CSV de cadastro da CVM. Retorna dict {cnpj_clean: {nome, setor, uf, ...}}"""
        resp = await self._client.get(CVM_CAD_URL)
        resp.raise_for_status()

        # CVM usa encoding latin-1
        text = resp.content.decode("latin-1", errors="replace")
        reader = csv.DictReader(io.StringIO(text), delimiter=";")

        cadastro: dict[str, dict] = {}
        for row in reader:
            cnpj_raw = row.get("CNPJ_CIA", "") or row.get("CNPJ", "")
            cnpj_clean = re.sub(r"\D", "", cnpj_raw)
            if len(cnpj_clean) != 14:
                continue
            cadastro[cnpj_clean] = {
                "nome": row.get("DENOM_SOCIAL", "") or row.get("DENOM_COMERC", ""),
                "setor": row.get("SETOR_ATIV", "") or row.get("CATEG_REG", ""),
                "situacao": row.get("SIT", "") or row.get("SIT_REG", ""),
                "uf": (row.get("UF", "") or "").upper().strip(),
                "municipio": (row.get("MUN", "") or "").title(),
            }
        return cadastro

    async def _fetch_receita(self) -> dict[str, float]:
        """
        Baixa DFP do último ano disponível e extrai receita líquida (conta 3.01).
        Retorna dict {cnpj_clean: receita_em_reais}.
        """
        # Tenta os últimos 3 anos em ordem decrescente
        current_year = datetime.now().year
        for year in range(current_year - 1, current_year - 4, -1):
            try:
                url = f"{CVM_DFP_BASE}dfp_cia_aberta_{year}.zip"
                resp = await self._client.get(url)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()

                # Extrair CSV de DRE consolidada do zip em memória
                zf = zipfile.ZipFile(io.BytesIO(resp.content))
                # Preferir consolidado, fallback individual
                target = None
                for name in zf.namelist():
                    if "DRE_con" in name and name.endswith(".csv"):
                        target = name
                        break
                if not target:
                    for name in zf.namelist():
                        if "DRE_ind" in name and name.endswith(".csv"):
                            target = name
                            break
                if not target:
                    continue

                content = zf.read(target).decode("latin-1", errors="replace")
                reader = csv.DictReader(io.StringIO(content), delimiter=";")

                receita_map: dict[str, float] = {}
                for row in reader:
                    if row.get("CD_CONTA", "").strip() != RECEITA_LIQUIDA_CODE:
                        continue
                    cnpj_raw = row.get("CNPJ_CIA", "")
                    cnpj_clean = re.sub(r"\D", "", cnpj_raw)
                    if len(cnpj_clean) != 14:
                        continue
                    val_str = row.get("VL_CONTA", "0") or "0"
                    try:
                        # CVM reporta em R$ mil (escala 1000)
                        val = float(val_str.replace(",", ".")) * 1000
                    except ValueError:
                        continue
                    # Pegar o valor mais recente (maior DT_REFER)
                    if cnpj_clean not in receita_map or val > receita_map[cnpj_clean]:
                        receita_map[cnpj_clean] = val

                print(f"[CVM] DFP {year} carregado: {len(receita_map)} empresas com receita")
                return receita_map

            except Exception as e:
                print(f"[CVM] DFP {year} falhou: {e}")
                continue

        return {}
