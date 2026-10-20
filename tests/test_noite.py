"""A noite inteira (scripts/noite.py): falas da linha de áudio -> sessão -> tela e publicador, com a base
atualizando em segundo plano, o 00:00 marcado uma vez para a sessão e para as falas, erros de cada etapa na
tela e retomada sem perder estado nem republicar.

A linha de áudio é trocada por uma linha falsa que entrega falas prontas (a linha de verdade tem a sua
medição no relatório do ticket 09); o Claude é o dublê de test_sessao; a busca roda de verdade sobre a
base de exemplo."""
import json
import threading
import time
import urllib.request
from datetime import timedelta

import pytest

from base_checagens import Indice
from falas import Fala
from noite import Noite, zero_da_gravacao
from publicador import ARQUIVO, Publicador
from test_publicador import commits, git, remoto  # noqa: F401 - remoto é fixture
from test_sessao import (CHECAGEM_DA_NOITE, DESEMPREGO, FALA_13, FALA_DESEMPREGO, FALA_MEDIACAO, FALA_PIX, FIXTURES,
                         FOF_13, INICIO, PIX, Claude, Relogio, afirmacao)


@pytest.fixture(scope="module")
def registros():
    return json.loads((FIXTURES / "base_sessao.json").read_text(encoding="utf-8"))["registros"]


@pytest.fixture
def indice(registros):
    return Indice(registros)


class LinhaFalsa:
    """Entrega as falas dadas (só as que terminam depois de `desde`), como a LinhaDeFalas: iterável,
    marcar_zero e parar. `pausa` entre falas; `falha_depois` levanta um erro depois de n falas."""

    def __init__(self, falas, desde=None, pausa=0.0, falha_depois=None, espera_parar=False):
        self.falas = [f for f in falas if desde is None or f.fim_s > desde]
        self.desde, self.pausa, self.falha_depois, self.espera_parar = desde, pausa, falha_depois, espera_parar
        self.zeros = []
        self._parar = threading.Event()

    def marcar_zero(self, instante):
        self.zeros.append(instante)

    def parar(self):
        self._parar.set()

    def __iter__(self):
        for k, f in enumerate(self.falas):
            if self.falha_depois is not None and k == self.falha_depois:
                raise RuntimeError("Whisper caiu")
            if self._parar.wait(self.pausa):
                return
            yield f
        if self.espera_parar:  # ao vivo: a linha só termina quando mandam parar
            self._parar.wait()


def falas_da_noite():
    return [Fala("Mediação", 0.0, 4.0, FALA_MEDIACAO), Fala("Lula", 300.0, 312.0, FALA_DESEMPREGO),
            Fala("Flávio", 900.0, 905.0, FALA_PIX)]


def test_falas_da_linha_chegam_a_sessao_e_o_painel_fica_gravado_na_pasta(indice, tmp_path):
    claude = Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_PIX: [PIX]})
    noite = Noite(tmp_path, indice, lambda desde: LinhaFalsa(falas_da_noite(), desde), chamar=claude,
                  relogio=Relogio(INICIO + timedelta(minutes=30)))

    noite.comeca()
    assert noite.espera(timeout=60)
    noite.encerra()

    assert claude.extracoes() == 2  # a fala da mediação não vai para a extração
    assert [p["trecho"] for p in noite.sessao.fila()] == [DESEMPREGO["trecho"], PIX["trecho"]]
    painel = json.loads((tmp_path / "painel.json").read_text(encoding="utf-8"))
    assert painel == noite.sessao.exporta()
    assert [json.loads(linha)["quem"] for linha in (tmp_path / "falas.jsonl").read_text(encoding="utf-8").splitlines()] \
        == ["Mediação", "Lula", "Flávio"]


def ate(condicao, segundos=30):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if valor := condicao():
            return valor
        time.sleep(0.02)
    raise AssertionError("condição não aconteceu a tempo")


class Linhas:
    """Fábrica de linhas falsas que guarda cada linha criada e o `desde` pedido."""

    def __init__(self, falas, kw=None, **opcoes):
        self.falas, self.kw, self.criadas = falas, kw if kw is not None else opcoes, []

    def __call__(self, desde):
        linha = LinhaFalsa(self.falas, desde, **(self.kw[len(self.criadas)] if isinstance(self.kw, list) else self.kw))
        self.criadas.append(linha)
        return linha


def test_marcar_o_00_00_na_tela_vale_para_a_sessao_e_para_a_linha_de_falas(indice, tmp_path):
    linhas = Linhas([], espera_parar=True)
    relogio = Relogio(INICIO)
    noite = Noite(tmp_path, indice, linhas, chamar=Claude({}), relogio=relogio)
    noite.comeca()
    ate(lambda: linhas.criadas)

    noite.sessao.marca_inicio()  # o que a tela faz no botão "Marcar 00:00 agora"

    assert linhas.criadas[0].zeros == [INICIO]
    assert noite.sessao.exporta()["meta"]["inicio_debate"] == INICIO.isoformat()
    noite.encerra()


def test_enter_no_terminal_marca_o_00_00_do_mesmo_jeito(indice, tmp_path):
    linhas = Linhas([], espera_parar=True)
    noite = Noite(tmp_path, indice, linhas, chamar=Claude({}), relogio=Relogio(INICIO))
    noite.comeca()
    ate(lambda: linhas.criadas)

    noite.marca_zero()

    assert linhas.criadas[0].zeros == [INICIO]
    assert noite.sessao.exporta()["meta"]["inicio_debate"] == INICIO.isoformat()
    noite.encerra()


def test_linha_que_cai_vai_para_os_erros_e_recomeca_da_ultima_fala_com_o_mesmo_00_00(indice, tmp_path):
    linhas = Linhas(falas_da_noite(), kw=[{"falha_depois": 2}, {}])
    claude = Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_PIX: [PIX]})
    noite = Noite(tmp_path, indice, linhas, chamar=claude, relogio=Relogio(INICIO + timedelta(minutes=30)),
                  espera_reinicio=0)
    noite.sessao.marca_inicio(INICIO)

    noite.comeca()
    assert noite.espera(timeout=60)
    noite.encerra()

    [erro] = noite.sessao.erros()
    assert erro["etapa"] == "transcricao" and "Whisper caiu" in erro["mensagem"]
    assert [linha.desde for linha in linhas.criadas] == [None, 312.0]  # recomeça depois da última fala entregue
    assert [linha.zeros for linha in linhas.criadas] == [[INICIO], [INICIO]]
    assert len(noite.sessao.fila()) == 2
    assert claude.extracoes() == 2  # nenhuma fala repetida


class ClaudeQueFalha(Claude):
    """Dublê que falha (como a API fora do ar) na extração das falas dadas."""

    def __init__(self, falas, falha_em):
        super().__init__(falas)
        self.falha_em = falha_em

    def __call__(self, etapa, prompt, conteudo, schema):
        if prompt == "extrair_afirmacoes.md" and any(f in conteudo for f in self.falha_em):
            raise ConnectionError("API do Claude fora do ar (529 overloaded)")
        return super().__call__(etapa, prompt, conteudo, schema)


def estado_da_tela(noite):
    with urllib.request.urlopen(f"http://127.0.0.1:{noite.tela.server_address[1]}/estado") as r:
        return json.loads(r.read())


def test_tela_mostra_os_erros_por_etapa_com_a_falha_do_claude_na_extracao(indice, tmp_path):
    claude = ClaudeQueFalha({FALA_DESEMPREGO: [DESEMPREGO], FALA_PIX: [PIX]}, falha_em=[FALA_DESEMPREGO])
    noite = Noite(tmp_path, indice, Linhas(falas_da_noite()), chamar=claude,
                  relogio=Relogio(INICIO + timedelta(minutes=30)), porta=0)
    noite.comeca()
    assert noite.espera(timeout=60)
    noite.sessao.registra_erro("publicar", "falha ao publicar: git push: rede")

    estado = estado_da_tela(noite)
    noite.encerra()

    paineis = {p["nome"]: p for p in estado["painel_erros"]}
    assert list(paineis) == ["Transcrição e áudio", "API do Claude", "API do Google (base)", "Publicação"]
    assert paineis["Transcrição e áudio"]["n"] == 0 and paineis["API do Google (base)"]["n"] == 0
    claude_api = paineis["API do Claude"]
    assert claude_api["n"] == 1
    assert "529 overloaded" in claude_api["ultimo"]["mensagem"] and claude_api["ultimo"]["quem"] == "Lula"
    assert paineis["Publicação"]["n"] == 1
    assert [p["trecho"] for p in estado["fila"]] == [PIX["trecho"]]  # a noite seguiu depois da falha
    assert estado["situacao"]["falas"] == 3


def painel_no_remoto(nu):
    return json.loads(git(nu, "show", f"main:{ARQUIVO}"))


def test_rodar_de_novo_na_mesma_pasta_segue_de_onde_parou_sem_chamar_o_claude_nem_republicar(indice, tmp_path, remoto):
    nu, repo = remoto
    pasta = tmp_path / "sessao"

    def noite(claude, minutos):
        n = Noite(pasta, indice, Linhas(falas_da_noite()), chamar=claude, relogio=Relogio(INICIO + timedelta(minutes=minutos)),
                  meta={"limiar_automatico": 0.9}, publicador=Publicador(repo, intervalo=0, espera_erro=0.1))
        n.comeca()
        assert n.espera(timeout=60)
        n.encerra()
        return n

    primeira = Noite(pasta, indice, Linhas([]), chamar=Claude({}), relogio=Relogio(INICIO))
    primeira.marca_zero(INICIO)
    primeira.encerra()
    claude = Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_PIX: [PIX]})
    noite(claude, 30)
    publicadas = [lig["trecho"] for lig in painel_no_remoto(nu)["ligacoes"]]
    assert sorted(publicadas) == sorted([DESEMPREGO["trecho"], PIX["trecho"]])  # nota ≥ 0,9 depois de 10 min
    envios = commits(nu)

    claude_de_novo = Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_PIX: [PIX]})
    segunda = noite(claude_de_novo, 40)  # outro horário: só meta.atualizado_em mudaria

    assert claude_de_novo.chamadas == []
    assert commits(nu) == envios  # nada mudou: nenhum commit novo
    assert sorted(lig["trecho"] for lig in segunda.sessao.exporta()["ligacoes"]) == sorted(publicadas)
    assert len((pasta / "falas.jsonl").read_text(encoding="utf-8").splitlines()) == 3


def test_ao_vivo_muito_tempo_sem_fala_depois_do_00_00_avisa_uma_vez_na_tela(indice, tmp_path):
    linhas = Linhas([], espera_parar=True)
    noite = Noite(tmp_path, indice, linhas, chamar=Claude({}), relogio=Relogio(INICIO), alerta_sem_fala_s=0.3)
    noite.comeca()
    time.sleep(0.6)
    assert noite.sessao.erros() == []  # antes do 00:00 o silêncio é normal

    noite.marca_zero()
    ate(lambda: noite.sessao.erros())
    time.sleep(0.6)
    noite.encerra()

    [erro] = noite.sessao.erros()
    assert erro["etapa"] == "audio" and "nenhuma fala" in erro["mensagem"]


def test_00_00_de_cada_gravacao_vem_do_json_ao_lado_ou_do_inicio_da_sessao(tmp_path):
    marcada = tmp_path / "gravacao-2120.wav"
    marcada.with_suffix(".json").write_text(json.dumps({"comecou_em": "2026-10-23T21:10:00.000", "zero_s": 612.5}))
    recomecada = tmp_path / "gravacao-2205.wav"  # a linha caiu e recomeçou: o 00:00 ficou antes da gravação
    recomecada.with_suffix(".json").write_text(json.dumps({"comecou_em": "2026-10-23T22:05:00.000"}))
    sem_json = tmp_path / "reserva.wav"

    assert zero_da_gravacao(marcada, INICIO) == 612.5
    assert zero_da_gravacao(recomecada, INICIO) == -45 * 60.0
    assert zero_da_gravacao(sem_json, INICIO) is None


def test_a_base_atualiza_em_segundo_plano_e_liga_o_que_estava_sem_par(registros, tmp_path):
    corte = afirmacao("vai cortar o décimo terceiro e as aposentadorias",
                      "Flávio Bolsonaro disse que, se eleito, vai cortar o 13º salário e as aposentadorias.",
                      "perene", {FOF_13: (0.9, "Mesma fala.")})
    chamadas = []

    def atualiza_base(ao_erro):
        chamadas.append(time.monotonic())
        if len(chamadas) == 1:  # a 1ª atualização (ao começar) termina com a afirmação já sem par
            ate(lambda: noite.sessao.exporta()["sem_par"])
        if len(chamadas) == 2:
            ao_erro("API do Google: 3 de 200 buscas falharam (HTTP 503)")
        if len(chamadas) == 3:
            raise ConnectionError("sem rede")
        return [CHECAGEM_DA_NOITE] if len(chamadas) >= 2 else []

    linhas = Linhas([Fala("Lula", 1500.0, 1510.0, FALA_13)], espera_parar=True)
    noite = Noite(tmp_path, Indice(registros), linhas, chamar=Claude({FALA_13: [corte]}),
                  relogio=Relogio(INICIO + timedelta(minutes=30)), atualiza_base=atualiza_base, intervalo_base=0.3)
    noite.comeca()

    [lig] = ate(lambda: noite.sessao.fila())
    ate(lambda: len(chamadas) >= 4)
    noite.encerra()

    assert (lig["trecho"], lig["ligada_depois"]) == (corte["trecho"], True)
    assert [(e["etapa"], e["mensagem"]) for e in noite.sessao.erros()] == [
        ("base", "API do Google: 3 de 200 buscas falharam (HTTP 503)"),
        ("base", "a atualização da base falhou (ConnectionError: sem rede)")]
