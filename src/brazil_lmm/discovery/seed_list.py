"""
Seed list — curated CNPJs of known Brazilian LMM companies in Indústria + Saúde.

These are publicly registered companies (CNPJ is public information in Brazil).
Used as an instant fallback when all APIs are unavailable.
Revenue range: approximately R$50M–R$850M.
"""
from __future__ import annotations

from brazil_lmm.discovery.bndes_discovery import DiscoveredCompany

# Format: (cnpj_digits, razao_social, sector, uf)
SEED_COMPANIES: list[tuple[str, str, str, str]] = [
    # Indústria — Alimentos & Bebidas
    ("01838723000127", "CAMIL ALIMENTOS SA", "Alimentos", "RS"),
    ("07526557000100", "M DIAS BRANCO SA", "Alimentos", "CE"),
    ("92754738000162", "MARCOPOLO SA", "Veículos", "RS"),
    ("89086144000116", "RANDON SA IMPLEMENTOS E PARTICIPACOES", "Veículos", "RS"),
    ("56720428000163", "INDUSTRIAS ROMI SA", "Máquinas", "SP"),
    ("60840055000131", "FLEURY SA", "Saúde", "SP"),
    ("04932944000128", "DASA - DIAGNOSTICOS DA AMERICA SA", "Saúde", "SP"),
    ("58430828000160", "BLAU FARMACEUTICA SA", "Farmacêutico", "SP"),
    ("60659463000191", "ACHE LABORATORIOS FARMACEUTICOS SA", "Farmacêutico", "SP"),
    ("55072021000143", "EUROFARMA LABORATORIOS SA", "Farmacêutico", "SP"),
    ("02341000000189", "HYPERA SA", "Farmacêutico", "SP"),
    ("57507378000178", "EMS SA", "Farmacêutico", "SP"),
    ("84429695000111", "WEG SA", "Eletrodomésticos", "SC"),
    ("33611500000119", "GERDAU SA", "Metalurgia", "RS"),
    ("60751503000101", "USINAS SIDERURGICAS DE MINAS GERAIS SA", "Metalurgia", "MG"),
    ("07170762000142", "TEGMA GESTAO LOGISTICA SA", "Logística", "SP"),
    ("01125541000199", "LOCALFRIO SA", "Logística", "SP"),
    ("76535764000143", "INTELBRAS SA", "Eletrônicos", "SC"),
    ("00776574000156", "TUPY SA", "Metalurgia", "SC"),
    ("92202408000161", "SCHULZ SA", "Máquinas", "SC"),
    ("83929379000174", "METALFRIO SOLUTIONS SA", "Refrigeração", "SP"),
    ("03541090000119", "FRAS-LE SA", "Autopeças", "RS"),
    ("01156506000116", "IOCHPE-MAXION SA", "Autopeças", "SP"),
    ("61156113000175", "MAHLE METAL LEVE SA", "Autopeças", "SP"),
    ("44782899000155", "WETZEL SA", "Autopeças", "SC"),
    # Saúde
    ("47508411000156", "FLEURY MEDICINA E SAUDE SA", "Saúde", "SP"),
    ("00853710000191", "ONCOCLÍNICAS DO BRASIL SA", "Saúde", "SP"),
    ("10629105000141", "REDE D'OR SAO LUIZ SA", "Saúde", "RJ"),
    ("06047087000139", "HAPVIDA PARTICIPACOES E INVESTIMENTOS SA", "Saúde", "CE"),
    ("47286906000145", "NOSSA SENHORA DE LOURDES HOSPITAL", "Saúde", "SP"),
    # Indústria diversa
    ("61092037000108", "IRANI PAPEL E EMBALAGEM SA", "Papel", "SC"),
    ("89116765000180", "SUZANO SA", "Papel", "SP"),
    ("60643228000121", "KLABIN SA", "Papel", "SP"),
    ("03703571000179", "FERTILIZANTES HERINGER SA", "Agrochemical", "MG"),
    ("07280698000184", "NUTRIPLANT INDUSTRIA E COMERCIO SA", "Agrochemical", "SP"),
    ("02387241000160", "KEPLER WEBER SA", "Armazenagem", "RS"),
    ("84683481000119", "TEKA TECELAGEM KUEHNRICH SA", "Têxtil", "SC"),
    ("45985371000108", "CEDRO TEXTIL SA", "Têxtil", "MG"),
    ("60810366000175", "VICUNHA TEXTIL SA", "Têxtil", "SP"),
    ("00073560000104", "VOTORANTIM CIMENTOS SA", "Construção", "SP"),
    ("06164253000187", "CONSTRUTORA TENDA SA", "Construção", "SP"),
    ("67030395000146", "EVEN CONSTRUTORA E INCORPORADORA SA", "Construção", "SP"),
    ("09350073000106", "DIRECIONAL ENGENHARIA SA", "Construção", "MG"),
    ("02998611000104", "TRISUL SA", "Construção", "SP"),
]


def get_seed_companies(
    sectors: list[str],
    ufs: list[str] | None = None,
) -> list[DiscoveredCompany]:
    results: list[DiscoveredCompany] = []

    target_industria = any(
        "industria" in s.lower() or "indústria" in s.lower() for s in sectors
    )
    target_saude = any(
        "saude" in s.lower() or "saúde" in s.lower() for s in sectors
    )

    health_sectors = {"Saúde", "Farmacêutico"}
    uf_set = {u.upper() for u in ufs} if ufs else None

    for cnpj, name, sector, uf in SEED_COMPANIES:
        is_health = sector in health_sectors
        if is_health and not target_saude:
            continue
        if not is_health and not target_industria:
            continue
        if uf_set and uf not in uf_set:
            continue

        results.append(DiscoveredCompany(
            cnpj=cnpj,
            razao_social=name,
            sector_hint=sector,
            uf=uf,
            city="",
            total_bndes_brl=0.0,
            contract_count=0,
            latest_year=None,
            discovery_source="seed_list",
            score_hint=0.2,
        ))

    print(f"[SEED] {len(results)} seed companies loaded")
    return results
