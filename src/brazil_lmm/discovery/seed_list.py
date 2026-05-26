"""
Seed list — CNPJs de empresas brasileiras genuinamente LMM.

Critério: receita estimada R$50M–R$850M, setores Indústria + Saúde.
Fontes: B3 (ITR/DFP público) + empresas privadas mid-size conhecidas.
CNPJs são informação pública (Receita Federal).
"""
from __future__ import annotations

from brazil_lmm.discovery.bndes_discovery import DiscoveredCompany

# Formato: (cnpj_digits, razao_social, sector, uf)
SEED_COMPANIES: list[tuple[str, str, str, str]] = [

    # -----------------------------------------------------------------------
    # MÁQUINAS & EQUIPAMENTOS — receita R$200M–R$700M
    # -----------------------------------------------------------------------
    ("56720428000163", "INDUSTRIAS ROMI SA",                          "Máquinas",             "SP"),
    ("92202408000161", "SCHULZ SA",                                   "Máquinas",             "SC"),
    ("44782899000155", "WETZEL SA",                                   "Autopeças",            "SC"),
    ("83929379000174", "METALFRIO SOLUTIONS SA",                      "Refrigeração",         "SP"),

    # -----------------------------------------------------------------------
    # PAPEL, EMBALAGEM & MATERIAIS DE CONSTRUÇÃO — receita R$400M–R$800M
    # -----------------------------------------------------------------------
    ("61092037000108", "IRANI PAPEL E EMBALAGEM SA",                  "Papel",                "SC"),
    ("83769980000103", "PORTOBELLO SA",                               "Cerâmica",             "SC"),
    ("56735130000101", "EUCATEX SA INDUSTRIA E COMERCIO",             "Madeira",              "SP"),
    ("07510163000140", "ETERNIT SA",                                  "Construção",           "SP"),

    # -----------------------------------------------------------------------
    # TÊXTIL & VESTUÁRIO — receita R$200M–R$700M
    # -----------------------------------------------------------------------
    ("45985371000108", "CEDRO TEXTIL SA",                             "Têxtil",               "MG"),
    ("84683481000119", "TEKA TECELAGEM KUEHNRICH SA",                 "Têxtil",               "SC"),
    ("83884191000126", "DOHLER SA",                                   "Têxtil",               "SC"),
    ("78876950000171", "CIA HERING SA",                               "Têxtil",               "SC"),

    # -----------------------------------------------------------------------
    # CONSTRUÇÃO CIVIL — receita R$300M–R$800M
    # -----------------------------------------------------------------------
    ("02998611000104", "TRISUL SA",                                   "Construção",           "SP"),
    ("05761634000147", "PLANO E PLANO CONSTRUCOES SA",                "Construção",           "SP"),
    ("04613875000107", "MELNICK DESENVOLVIMENTO IMOBILIARIO SA",      "Construção",           "RS"),
    ("01090657000182", "MOURA DUBEUX ENGENHARIA SA",                  "Construção",           "PE"),
    ("30038066000166", "LAVVI INCORPORADORA SA",                      "Construção",           "SP"),

    # -----------------------------------------------------------------------
    # AGRO, SEMENTES & INSUMOS — receita R$100M–R$800M
    # -----------------------------------------------------------------------
    ("07280698000184", "NUTRIPLANT INDUSTRIA E COMERCIO SA",          "Agrochemical",         "SP"),
    ("02387241000160", "KEPLER WEBER SA",                             "Máquinas Agrícolas",   "RS"),
    ("71995771000102", "BOA SAFRA SEMENTES SA",                       "Sementes",             "MS"),
    ("77102914000154", "NORTOX SA",                                   "Agrochemical",         "PR"),
    ("73423921000111", "OUROFINO SAUDE ANIMAL PARTICIPACOES SA",      "Agrochemical",         "SP"),

    # -----------------------------------------------------------------------
    # LOGÍSTICA & ARMAZENAGEM — receita R$300M–R$800M
    # -----------------------------------------------------------------------
    ("07170762000142", "TEGMA GESTAO LOGISTICA SA",                   "Logística",            "SP"),
    ("01125541000199", "LOCALFRIO SA",                                "Logística",            "SP"),

    # -----------------------------------------------------------------------
    # FARMACÊUTICO mid-size — receita R$100M–R$800M
    # -----------------------------------------------------------------------
    ("05159134000187", "PRATI-DONADUZZI SA",                          "Farmacêutico",         "PR"),
    ("60664619000193", "UNIAO QUIMICA FARMACEUTICA NACIONAL SA",      "Farmacêutico",         "SP"),
    ("50530060000164", "CRISTALIA PRODUTOS QUIMICOS FARMACEUTICOS",   "Farmacêutico",         "SP"),
    ("63543892000144", "LABORATORIO TEUTO BRASILEIRO SA",             "Farmacêutico",         "GO"),
    ("02575829000137", "NOVEFARMA INDUSTRIA E COMERCIO SA",           "Farmacêutico",         "SP"),

    # -----------------------------------------------------------------------
    # SAÚDE — hospitais e diagnósticos mid-size — receita R$100M–R$800M
    # -----------------------------------------------------------------------
    ("47286906000145", "NOSSA SENHORA DE LOURDES HOSPITAL",           "Saúde",                "SP"),
    ("10978364000109", "ALLIAR MEDICINA E DIAGNOSTICO SA",            "Saúde",                "SP"),
    ("71524610000147", "HOSPITAL MATER DEI SA",                       "Saúde",                "MG"),
    ("03823557000196", "KORA SAUDE PARTICIPACOES SA",                 "Saúde",                "RS"),
    ("07518901000110", "HAPVIDA NORTE NORDESTE LOGISTICA SA",         "Saúde",                "CE"),
    ("02975813000107", "HOSPITAL SANTA PAULA SA",                     "Saúde",                "SP"),

    # -----------------------------------------------------------------------
    # EQUIPAMENTOS MÉDICOS & DISPOSITIVOS — receita R$50M–R$400M
    # -----------------------------------------------------------------------
    ("04372495000170", "BAUMER SA",                                   "Equipamentos Médicos", "SP"),
    ("60725503000148", "INSTRAMED INDUSTRIA MEDICO CIRURGICA SA",     "Equipamentos Médicos", "RS"),

    # -----------------------------------------------------------------------
    # QUÍMICA & PLÁSTICOS — receita R$100M–R$600M
    # -----------------------------------------------------------------------
    ("49669856000117", "PLASTICOS ABC INDUSTRIAL SA",                 "Química",              "SP"),
    ("73191916000181", "OXITENO SA INDUSTRIA E COMERCIO",             "Química",              "SP"),

    # -----------------------------------------------------------------------
    # ELETROELETRÔNICO mid-size — receita R$100M–R$600M
    # -----------------------------------------------------------------------
    ("81243735000148", "POSITIVO TECNOLOGIA SA",                      "Eletrônicos",          "PR"),
    ("01578931000100", "MULTILASER INDUSTRIAL SA",                    "Eletrônicos",          "SP"),
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

    health_sectors = {"Saúde", "Farmacêutico", "Equipamentos Médicos"}
    uf_set = {u.upper() for u in ufs} if ufs else None

    seen: set[str] = set()
    for cnpj, name, sector, uf in SEED_COMPANIES:
        if cnpj in seen:
            continue
        seen.add(cnpj)

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
            score_hint=0.3,
        ))

    print(f"[SEED] {len(results)} seed companies loaded")
    return results
