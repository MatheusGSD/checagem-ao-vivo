"""Reconhecimento de quem fala (scripts/vozes.py), visto pelo que a LinhaDeFalas entrega: cada fala sai
com o perfil de voz mais parecido, ou "Mediação" quando nenhum perfil passa do limiar.

O áudio, a detecção de voz e o Whisper são os dublês de test_falas.Mundo; o reconhecedor é o de
verdade (perfis, limiar e perfis.json), com o modelo ECAPA trocado por um codificador dublê que devolve o
"jeito de falar" de cada voz do roteiro.
"""
import numpy as np

import vozes
from test_falas import Mundo

LULA, FLAVIO = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]
# como soa cada voz do roteiro para o codificador; o jornalista não se parece com nenhum candidato
VOZ = {"Lula": [0.9, 0.2, 0.1], "Flávio": [0.1, 0.95, 0.0], "Mediação": [0.3, 0.2, 0.9]}


def codificador(mundo):
    def codifica(sinal):
        x = np.round(np.asarray(sinal, dtype=np.float64), 4)
        valores, n = np.unique(x[x != 0], return_counts=True)
        return np.asarray(VOZ[mundo.vozes[float(valores[n.argmax()])]], dtype=np.float32)

    return codifica


def falas_com(mundo, reconhecedor):
    mundo.reconhecedor = lambda: reconhecedor
    return [(q, t) for q, _, _, t in mundo.falas(zero=0.0)]


ROTEIRO = [("Lula", 4.0, "Pergunto ao candidato."), (None, 1.0), ("Flávio", 4.0, "Respondo já."), (None, 1.0),
           ("Mediação", 3.0, "Tempo esgotado.")]


def test_cada_fala_sai_com_o_perfil_mais_parecido_e_a_voz_que_nao_parece_com_nenhum_vai_para_mediacao():
    mundo = Mundo(ROTEIRO)
    r = vozes.Reconhecedor({"Lula": LULA, "Flávio": FLAVIO}, limiar=0.5, codificador=codificador(mundo))

    assert falas_com(mundo, r) == [("Lula", "Pergunto ao candidato."), ("Flávio", "Respondo já."),
                                   ("Mediação", "Tempo esgotado.")]


def test_com_limiar_mais_alto_que_a_semelhanca_tudo_vai_para_mediacao():
    mundo = Mundo(ROTEIRO)
    r = vozes.Reconhecedor({"Lula": LULA, "Flávio": FLAVIO}, limiar=0.999, codificador=codificador(mundo))

    assert [q for q, _ in falas_com(mundo, r)] == ["Mediação"]  # uma voz só, sem troca de locutor


def test_perfis_e_limiar_gravados_em_disco_valem_ao_carregar(tmp_path):
    vozes.gravar_perfis(tmp_path, {"Lula": LULA, "Flávio": FLAVIO}, limiar=0.42)
    mundo = Mundo(ROTEIRO)

    r = vozes.Reconhecedor.carregar(tmp_path, codificador=codificador(mundo))

    assert [q for q, _ in falas_com(mundo, r)] == ["Lula", "Flávio", "Mediação"]
