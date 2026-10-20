"""Contrato do arquivo de dados da página da Globo (docs/globo/data/painel.json; exemplo em tests/fixtures/).

O ponto testado é `contrato_painel.valida(dados) -> list[str]`: lista vazia quando o arquivo segue o
contrato, uma mensagem por problema (com o caminho do campo) quando não segue.
"""
import copy
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

from contrato_painel import valida  # noqa: E402

PAGINA = RAIZ / "docs" / "globo" / "data"
# o exemplo fictício fica fora do site publicado (docs/), só nos testes
ARQUIVOS = {"painel.json": PAGINA / "painel.json", "exemplo.json": RAIZ / "tests" / "fixtures" / "exemplo.json"}


def carrega(nome):
    return json.loads(ARQUIVOS[nome].read_text(encoding="utf-8"))


def test_arquivo_vazio_de_antes_do_debate_segue_o_contrato():
    assert valida(carrega("painel.json")) == []


def problemas_com(mexe, base="painel.json"):
    dados = copy.deepcopy(carrega(base))
    mexe(dados)
    return valida(dados)


def tem(problemas, caminho):
    return any(p.startswith(caminho + ":") for p in problemas)


def test_metadado_obrigatorio_ausente_aponta_o_campo():
    assert tem(problemas_com(lambda d: d["meta"].pop("atualizado_em")), "meta.atualizado_em")
    assert tem(problemas_com(lambda d: d["meta"].pop("agencias_excluidas")), "meta.agencias_excluidas")


def test_limiar_e_nulo_quando_o_automatico_esta_desligado_ou_uma_nota_entre_0_e_1():
    def limiar(v):
        return lambda d: d["meta"].update(limiar_automatico=v)

    assert problemas_com(limiar(None)) == []
    assert problemas_com(limiar(0.9)) == []
    assert tem(problemas_com(limiar(1.5)), "meta.limiar_automatico")
    assert tem(problemas_com(limiar("0.9")), "meta.limiar_automatico")


def test_campo_com_nome_fora_do_contrato_e_apontado():
    assert tem(problemas_com(lambda d: d["meta"].update(atualizado=1)), "meta.atualizado")


def test_arquivo_de_exemplo_segue_o_contrato():
    assert valida(carrega("exemplo.json")) == []


def test_site_publicado_nao_tem_dados_de_exemplo():
    for arq in (RAIZ / "docs").rglob("*.json"):
        dados = json.loads(arq.read_text(encoding="utf-8"))
        assert not (isinstance(dados, dict) and dados.get("meta", {}).get("exemplo")), arq


def test_ligacao_so_aceita_os_valores_combinados():
    def troca(campo, valor):
        return lambda d: d["ligacoes"][0].update({campo: valor})

    assert tem(problemas_com(troca("origem", "manual"), "exemplo.json"), "ligacoes[0].origem")
    assert tem(problemas_com(troca("quem", "Mediação"), "exemplo.json"), "ligacoes[0].quem")
    assert tem(problemas_com(troca("tipo", "estatistica"), "exemplo.json"), "ligacoes[0].tipo")
    assert tem(problemas_com(troca("minutagem_s", -3), "exemplo.json"), "ligacoes[0].minutagem_s")


def test_checagem_diz_de_onde_vem_o_rotulo_e_traz_data_do_dia_ou_nulo():
    def troca(campo, valor):
        return lambda d: d["ligacoes"][0]["checagens"][0].update({campo: valor})

    assert problemas_com(troca("data", None), "exemplo.json") == []
    assert tem(problemas_com(troca("data", "11/02/2026"), "exemplo.json"), "ligacoes[0].checagens[0].data")
    assert tem(problemas_com(troca("fonte_rotulo", "claimreview"), "exemplo.json"),
               "ligacoes[0].checagens[0].fonte_rotulo")


def test_ligacao_sem_nenhuma_checagem_nao_entra():
    assert tem(problemas_com(lambda d: d["ligacoes"][0].update(checagens=[]), "exemplo.json"),
               "ligacoes[0].checagens")


def test_afirmacao_sem_par_traz_minutagem_quem_trecho_e_reformulacao():
    assert tem(problemas_com(lambda d: d["sem_par"][0].pop("reformulacao"), "exemplo.json"),
               "sem_par[0].reformulacao")
    assert tem(problemas_com(lambda d: d["sem_par"][0].update(quem="Bonner"), "exemplo.json"), "sem_par[0].quem")


def test_correcao_guarda_horario_motivo_e_a_ligacao_retirada_inteira():
    assert tem(problemas_com(lambda d: d["correcoes"][0].update(em="22:12"), "exemplo.json"), "correcoes[0].em")
    assert tem(problemas_com(lambda d: d["correcoes"][0].update(motivo=""), "exemplo.json"), "correcoes[0].motivo")
    assert tem(problemas_com(lambda d: d["correcoes"][0]["retirado"].pop("checagens"), "exemplo.json"),
               "correcoes[0].retirado.checagens")


def test_correcao_de_rotulo_guarda_horario_ligacao_e_o_rotulo_antigo_e_o_novo_com_a_fonte():
    [rotulo] = [c for c in carrega("exemplo.json")["correcoes"] if c["tipo"] == "rotulo"]
    i = carrega("exemplo.json")["correcoes"].index(rotulo)

    def mexe(f):
        return problemas_com(lambda d: f(d["correcoes"][i]), "exemplo.json")

    assert tem(mexe(lambda c: c.pop("rotulo_anterior")), f"correcoes[{i}].rotulo_anterior")
    assert tem(mexe(lambda c: c.update(fonte_rotulo="claimreview")), f"correcoes[{i}].fonte_rotulo")
    assert tem(mexe(lambda c: c.update(fonte_rotulo_anterior=None)), f"correcoes[{i}].fonte_rotulo_anterior")
    # campos de uma despublicação não cabem numa correção de rótulo
    assert tem(mexe(lambda c: c.update(motivo="selo errado")), f"correcoes[{i}].motivo")


def test_correcao_diz_se_e_despublicacao_ou_rotulo():
    assert tem(problemas_com(lambda d: d["correcoes"][0].update(tipo="edicao"), "exemplo.json"), "correcoes[0].tipo")
    assert tem(problemas_com(lambda d: d["correcoes"][0].pop("tipo"), "exemplo.json"), "correcoes[0].tipo")


def test_identificador_de_ligacao_nao_se_repete():
    assert tem(problemas_com(lambda d: d["ligacoes"][1].update(id="lig-0004"), "exemplo.json"), "ligacoes[1].id")
