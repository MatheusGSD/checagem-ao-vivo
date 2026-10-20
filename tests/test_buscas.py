"""Lista de buscas (data/buscas_checagens.json): variantes curtas das buscas longas que voltaram vazias.

Pontos testados: `termos_de_busca(arquivo)` (o que a coleta busca) e a linha de cobertura das
afirmações da Band em `relatorio(registros, afirmacoes)`.
"""
import json

from base_checagens import afirmacoes_com_variantes, relatorio, termos_de_busca

BUSCAS = {
    "nomes": ["Lula"],
    "temas_telao": ["saúde pública"],
    "regioes": ["Lula Norte"],
    "assuntos": {"saude": ["SUS"]},
    "afirmacoes_band": [{"t": 1.0, "quem": "Lula", "busca": "O governo Lula criou o Pé-de-Meia para reduzir a evasão."},
                        {"t": 2.0, "quem": "Flávio", "busca": "Lula disputa sua sétima eleição presidencial."}],
    "variantes_curtas": {"Lula Norte": ["Lula Amazônia"],
                         "O governo Lula criou o Pé-de-Meia para reduzir a evasão.": ["Pé-de-Meia evasão", "Lula"]},
}


def arquivo(tmp_path, d=BUSCAS):
    p = tmp_path / "buscas.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


def test_variantes_curtas_entram_depois_das_buscas_originais_sem_repetir(tmp_path):
    assert termos_de_busca(arquivo(tmp_path)) == [
        "Lula", "saúde pública", "Lula Norte", "SUS",
        "O governo Lula criou o Pé-de-Meia para reduzir a evasão.", "Lula disputa sua sétima eleição presidencial.",
        "Lula Amazônia", "Pé-de-Meia evasão"]


def test_afirmacao_da_band_conta_como_coberta_se_a_original_ou_uma_variante_trouxe_resultado(tmp_path):
    afirmacoes = afirmacoes_com_variantes(arquivo(tmp_path))
    registros = [{"agencia": "Aos Fatos", "data": "2026-01-01", "data_origem": "revisao",
                  "buscas": ["Pé-de-Meia evasão"]}]
    assert "Afirmações da Band com algum resultado na busca: 1 de 2" in relatorio(registros, afirmacoes)


def test_lista_do_repo_tem_variantes_curtas_de_2_a_5_palavras_para_buscas_que_existem():
    from base_checagens import RAIZ

    caminho = RAIZ / "data" / "buscas_checagens.json"
    d = json.loads(caminho.read_text(encoding="utf-8"))
    originais = set(termos_de_busca(caminho)) - {v for vs in d["variantes_curtas"].values() for v in vs}
    assert set(d["variantes_curtas"]) <= originais
    for variantes in d["variantes_curtas"].values():
        assert variantes and all(2 <= len(v.split()) <= 5 for v in variantes)
