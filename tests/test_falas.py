"""Do áudio às falas (scripts/falas.py): a LinhaDeFalas recebe áudio e entrega falas fechadas.

Uma fala fecha quando muda quem fala, depois de 60 s, depois de um silêncio longo ou no fim da fonte; a
minutagem conta do 00:00 marcado pelo operador. A fonte de áudio, a detecção de voz, o Whisper e o
reconhecimento de quem fala são trocados por dublês que combinam entre si: cada trecho de voz do roteiro
vira um sinal constante, com um valor próprio, que o dublê do Whisper "transcreve" como o texto do roteiro
e o dublê do reconhecedor atribui ao locutor do roteiro. O que se testa é só o que sai da linha.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from falas import LinhaDeFalas

TAXA = 16000
BASE = {"Lula": 0.2, "Flávio": 0.4, "Mediação": 0.6}


class Mundo:
    """Roteiro do áudio: [("Lula", segundos, "texto"), (None, segundos) = silêncio, ("zero",) = o operador
    marca o 00:00 neste ponto do áudio, ("zero", t) = o operador marca, neste ponto, o 00:00 no segundo t da
    fonte, que já passou]. Gera a fonte e os dublês que leem o mesmo sinal."""

    def __init__(self, roteiro, inicio=0.0):
        self.roteiro, self.inicio = roteiro, inicio
        self.textos, self.vozes = {}, {}
        self.linha = None

    def _valor(self, quem, texto):
        v = round(BASE[quem] + 0.001 * (len(self.textos) + 1), 4)
        self.textos[v], self.vozes[v] = texto, quem
        return v

    def _le(self, sinal):
        """(início, fim, valor) de cada trecho de sinal constante não nulo, em segundos do sinal."""
        x = np.round(np.asarray(sinal, dtype=np.float64), 4)
        trechos, k = [], 0
        while k < len(x):
            if x[k] == 0:
                k += 1
                continue
            j = k
            while j < len(x) and x[j] == x[k]:
                j += 1
            trechos.append((k / TAXA, j / TAXA, float(x[k])))
            k = j
        return trechos

    # ---- fonte de áudio (o que a LinhaDeFalas pede de FonteArquivo / FonteLoopback)
    def fonte(self):
        mundo = self

        class Fonte:
            inicio = mundo.inicio

            def __init__(self):
                self.amostras = 0

            def posicao(self):
                return self.inicio + self.amostras / TAXA

            def parar(self):
                pass

            def blocos(self):
                for item in mundo.roteiro:
                    if item[0] == "zero":
                        mundo.linha.marcar_zero(item[1] if len(item) > 1 else self.posicao())
                        continue
                    quem, segundos = item[0], item[1]
                    valor = 0.0 if quem is None else mundo._valor(quem, item[2])
                    sinal = np.full(int(round(segundos * TAXA)), valor, dtype=np.float32)
                    for a in range(0, len(sinal), 1600):
                        bloco = sinal[a:a + 1600]
                        self.amostras += len(bloco)
                        yield bloco
                for _ in range(30):  # 3 s de silêncio no fim, como depois do último bloco
                    self.amostras += 1600
                    yield np.zeros(1600, dtype=np.float32)

        return Fonte()

    # ---- detecção de voz, Whisper e quem fala
    def vad(self):
        return SimpleNamespace(prob=lambda chunk: 1.0 if np.abs(chunk).max() > 0.01 else 0.0)

    def transcritor(self):
        return SimpleNamespace(palavras=lambda sinal: [(a, b, " " + self.textos[v]) for a, b, v in self._le(sinal)])

    def reconhecedor(self):
        def quem_fala(sinal):
            if len(sinal) < 0.5 * TAXA:
                return SimpleNamespace(quem=None, semelhanca=float("nan"))
            trechos = self._le(sinal)
            maior = max(trechos, key=lambda t: t[1] - t[0])
            return SimpleNamespace(quem=self.vozes[maior[2]], semelhanca=0.9)

        return SimpleNamespace(quem_fala=quem_fala)

    def falas(self, zero=None, **kw):
        self.linha = LinhaDeFalas(self.fonte(), self.transcritor(), self.reconhecedor(), zero=zero,
                                  vad=self.vad(), atribuicao="janelas", **kw)
        return [(f.quem, f.inicio_s, f.fim_s, f.texto) for f in self.linha]


def aprox(falas):
    return [(q, pytest.approx(i, abs=0.05), pytest.approx(f, abs=0.05), t) for q, i, f, t in falas]


def test_quando_muda_quem_fala_a_fala_anterior_fecha():
    falas = Mundo([("Lula", 3.0, "Olha, pergunta simples"), (None, 1.0), ("Lula", 3.0, "ao candidato Flávio."),
                   (None, 1.0), ("Flávio", 2.0, "Eu respondo.")]).falas(zero=0.0)

    assert falas == aprox([("Lula", 0.0, 7.0, "Olha, pergunta simples ao candidato Flávio."),
                           ("Flávio", 8.0, 10.0, "Eu respondo.")])


def test_fala_de_mais_de_60_s_fecha_aos_60_s_e_continua_numa_nova():
    roteiro = []
    for k in range(7):  # Lula fala 70 s, em trechos de 9 s com pausas de 1 s
        roteiro += [("Lula", 9.0, f"p{k}"), (None, 1.0)]

    falas = Mundo(roteiro).falas(zero=0.0)

    assert falas == aprox([("Lula", 0.0, 59.0, "p0 p1 p2 p3 p4 p5"), ("Lula", 60.0, 69.0, "p6")])


def test_silencio_longo_fecha_a_fala_em_andamento():
    falas = Mundo([("Flávio", 4.0, "Encerro aqui."), (None, 10.0), ("Flávio", 3.0, "Voltando do intervalo.")]
                  ).falas(zero=0.0)

    assert [t for _, _, _, t in falas] == ["Encerro aqui.", "Voltando do intervalo."]


def test_pausa_curta_nao_fecha_a_fala():
    falas = Mundo([("Flávio", 4.0, "Primeiro ponto."), (None, 3.0), ("Flávio", 3.0, "Segundo ponto.")]
                  ).falas(zero=0.0)

    assert [t for _, _, _, t in falas] == ["Primeiro ponto. Segundo ponto."]


def test_minutagem_conta_do_00_00_marcado_pelo_operador():
    mundo = Mundo([(None, 10.0), ("Mediação", 5.0, "Boa noite."), (None, 1.0), ("Lula", 4.0, "Boa noite a todos.")],
                  inicio=15570.0)  # a fonte começa no segundo 15570 da gravação

    falas = mundo.falas(zero=15578.5)

    assert falas == aprox([("Mediação", 1.5, 6.5, "Boa noite."), ("Lula", 7.5, 11.5, "Boa noite a todos.")])


def test_sem_00_00_marcado_nada_sai_e_a_marcacao_libera_so_o_que_termina_depois_dela():
    falas = Mundo([("Mediação", 5.0, "Vinheta e apresentação."), (None, 8.0), ("Lula", 3.0, "Teste de som."),
                   (None, 1.0), ("Mediação", 4.0, "Está começando o debate."), (None, 2.0),
                   ("zero", 14.0),  # o operador marca com atraso: o 00:00 foi no segundo 14, que já passou
                   (None, 1.0), ("Flávio", 3.0, "Boa noite.")]).falas()

    # a vinheta terminou antes do 00:00 e fica de fora; o teste de som, em andamento nele, sai com o
    # começo de verdade, antes do 00:00
    assert falas == aprox([("Lula", -1.0, 2.0, "Teste de som."), ("Mediação", 3.0, 7.0, "Está começando o debate."),
                           ("Flávio", 10.0, 13.0, "Boa noite.")])


def test_fala_em_andamento_quando_o_00_00_e_marcado_sai_com_os_tempos_verdadeiros():
    # o candidato falava havia 34 s quando o operador marcou o 00:00 (como no ensaio de 20/10)
    falas = Mundo([("Flávio", 11.0, "Eu vou recuperar cada metro."), (None, 0.6), ("Flávio", 11.0, "Cada rua."),
                   (None, 0.6), ("Flávio", 11.0, "Cada bairro."), ("zero",), ("Flávio", 0.5, "E as cadeias."),
                   (None, 1.0), ("Lula", 3.0, "Deixa eu falar.")]).falas()

    # começou 34,2 s antes do 00:00 e terminou meio segundo depois dele: a duração é a de verdade
    assert falas == aprox([("Flávio", -34.2, 0.5, "Eu vou recuperar cada metro. Cada rua. Cada bairro. E as cadeias."),
                           ("Lula", 1.5, 4.5, "Deixa eu falar.")])


def test_pedaco_curto_demais_para_saber_quem_fala_fica_com_a_fala_em_volta():
    falas = Mundo([("Lula", 5.0, "O senhor votou contra."), (None, 0.6), ("Lula", 0.4, "Contra."), (None, 0.6),
                   ("Flávio", 3.0, "Não votei.")]).falas(zero=0.0)

    assert [(q, t) for q, _, _, t in falas] == [("Lula", "O senhor votou contra. Contra."), ("Flávio", "Não votei.")]


def test_minutagem_sai_em_centesimos_de_segundo():
    falas = Mundo([(None, 1.1), ("Lula", 2.7234, "Boa noite.")]).falas(zero=0.0)

    [(_, inicio, fim, _)] = falas
    assert (round(inicio, 2), round(fim, 2)) == (inicio, fim)
