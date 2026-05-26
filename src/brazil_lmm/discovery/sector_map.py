"""
CNAE prefix sets for each target sector.
Two-digit CNAE division codes (as strings, zero-padded).
"""

# Indústria — manufacturing, processing, machinery, inputs
INDUSTRY_CNAES: set[str] = {
    "10",  # Alimentos
    "11",  # Bebidas
    "13",  # Têxtil
    "14",  # Vestuário
    "15",  # Couro e calçados
    "16",  # Madeira
    "17",  # Papel e celulose
    "19",  # Petróleo e derivados
    "20",  # Química
    "21",  # Farmacêutico
    "22",  # Borracha e plástico
    "23",  # Minerais não-metálicos
    "24",  # Metalurgia
    "25",  # Produtos de metal
    "26",  # Eletrônicos
    "27",  # Eletrodomésticos
    "28",  # Máquinas e equipamentos
    "29",  # Veículos
    "30",  # Outros veículos
    "31",  # Móveis
    "32",  # Produtos diversos
    "33",  # Manutenção industrial
    "41",  # Construção civil (buildings)
    "42",  # Obras de infraestrutura
    "43",  # Serviços especializados de construção
}

# Saúde — healthcare, pharma, medical devices, health tech
HEALTH_CNAES: set[str] = {
    "21",  # Farmacêutico (também em indústria)
    "32",  # Equipamentos médicos / odontológicos
    "46",  # Comércio atacadista de farmacêuticos
    "47",  # Farmácias (varejo)
    "72",  # P&D (pesquisa em saúde)
    "75",  # Veterinária
    "86",  # Atividades de atenção à saúde humana
    "87",  # Atividades de atenção residencial
    "88",  # Serviços sociais
}

# All target CNAEs combined
ALL_TARGET_CNAES: set[str] = INDUSTRY_CNAES | HEALTH_CNAES
