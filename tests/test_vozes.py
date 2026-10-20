"""Reconhecimento de quem fala: classificação de um trecho pelos perfis de voz, com limiar para a mediação.

O modelo ECAPA é trocado por um dublê que devolve um vetor fixo por trecho; o que se testa é a regra de
decisão (perfil mais parecido, ou "Mediação" abaixo do limiar) e os perfis gravados e lidos do disco.
"""
import numpy as np

import vozes

TAXA = 16000
LULA = np.array([1.0, 0.0, 0.0])
FLAVIO = np.array([0.0, 1.0, 0.0])


def dubles(vetor):
    """Codificador dublê: devolve sempre o mesmo vetor, seja qual for o áudio."""
    return lambda sinal: np.asarray(vetor, dtype=np.float32)


def segundos(s):
    return np.zeros(int(s * TAXA), dtype=np.float32)


def test_trecho_parecido_com_um_perfil_e_atribuido_a_ele():
    r = vozes.Reconhecedor({"Lula": LULA, "Flávio": FLAVIO}, limiar=0.5, codificador=dubles([0.9, 0.2, 0.1]))
    v = r.quem_fala(segundos(3))
    assert v.quem == "Lula"
    assert v.semelhanca > 0.9


def test_trecho_pouco_parecido_com_os_dois_perfis_vai_para_mediacao():
    r = vozes.Reconhecedor({"Lula": LULA, "Flávio": FLAVIO}, limiar=0.5, codificador=dubles([0.3, 0.2, 0.9]))
    v = r.quem_fala(segundos(3))
    assert v.quem == "Mediação"
    assert v.semelhancas["Lula"] > v.semelhancas["Flávio"]


def test_trecho_curto_demais_fica_sem_locutor():
    def codificador(sinal):
        raise AssertionError("trecho curto não deveria ir ao modelo")

    r = vozes.Reconhecedor({"Lula": LULA, "Flávio": FLAVIO}, limiar=0.5, codificador=codificador)
    assert r.quem_fala(segundos(0.3)).quem is None


def test_perfis_gravados_sao_lidos_de_volta_com_o_limiar(tmp_path):
    vozes.gravar_perfis(tmp_path, {"Lula": LULA, "Flávio": FLAVIO}, limiar=0.42, extra={"janelas": {"Lula": 3}})
    r = vozes.Reconhecedor.carregar(tmp_path, codificador=dubles([0.1, 0.95, 0.0]))
    assert r.limiar == 0.42
    assert r.quem_fala(segundos(2)).quem == "Flávio"
