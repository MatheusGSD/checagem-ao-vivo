"""Publicador: cada mudança pública da sessão chega ao ramo publicado do remoto, num commit que só toca o
arquivo de dados da página, com pushes agrupados e falhas avisadas e tentadas de novo.

Os testes usam um repositório git de verdade, clonado de um remoto nu em tmp_path."""
import json
import subprocess
import time

import pytest

from publicador import ARQUIVO, Publicador


def git(pasta, *args):
    r = subprocess.run(["git", "-C", str(pasta), *args], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def dados(n):
    """Um arquivo de dados com n ligações (o conteúdo não importa para o publicador)."""
    return {"versao_contrato": 1, "meta": {"titulo": "teste", "atualizado_em": f"2026-10-23T21:{n:02d}:00-03:00"},
            "ligacoes": [{"id": f"l{i}", "trecho": "Comentário com acento: ção"} for i in range(n)],
            "sem_par": [], "correcoes": []}


def ate(condicao, segundos=15):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if valor := condicao():
            return valor
        time.sleep(0.05)
    raise AssertionError("condição não aconteceu a tempo")


@pytest.fixture
def remoto(tmp_path, monkeypatch):
    """Remoto nu com main contendo um README e um painel.json inicial; devolve (remoto, clone de trabalho)."""
    for k, v in {"GIT_AUTHOR_NAME": "Teste", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "Teste",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    nu = tmp_path / "remoto.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(nu)], check=True)
    semente = tmp_path / "semente"
    subprocess.run(["git", "clone", "-q", str(nu), str(semente)], check=True, capture_output=True)
    (semente / "README.md").write_text("oi\n", encoding="utf-8")
    (semente / ARQUIVO).parent.mkdir(parents=True)
    (semente / ARQUIVO).write_text("{}\n", encoding="utf-8")
    git(semente, "add", ".")
    git(semente, "commit", "-q", "-m", "início")
    git(semente, "push", "-q", "origin", "HEAD:main")
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(nu), str(repo)], check=True, capture_output=True)
    return nu, repo


def commits(nu):
    return git(nu, "log", "--format=%s", "main").splitlines()


def publicado(nu):
    return json.loads(git(nu, "show", f"main:{ARQUIVO}"))


@pytest.fixture
def abre():
    abertos = []

    def _abre(repo, **kw):
        p = Publicador(repo, **kw)
        abertos.append(p)
        return p

    yield _abre
    for p in abertos:
        p.encerra(timeout=5)


def test_mudanca_publicada_vira_commit_so_do_arquivo_de_dados_no_remoto(remoto, abre):
    nu, repo = remoto
    p = abre(repo)
    p.notifica(dados(2))
    ate(lambda: len(commits(nu)) == 2)
    assert publicado(nu) == dados(2)
    assert git(nu, "show", "--format=", "--name-only", "main") == ARQUIVO
    assert commits(nu)[0].startswith("Checagem ao vivo: painel atualizado")
    assert json.loads((repo / ARQUIVO).read_text(encoding="utf-8")) == dados(2)


def test_mudancas_seguidas_viram_um_push_por_intervalo_com_o_conteudo_mais_recente(remoto, abre):
    nu, repo = remoto
    p = abre(repo, intervalo=2)
    p.notifica(dados(1))
    ate(lambda: len(commits(nu)) == 2)
    for n in (2, 3, 4):
        p.notifica(dados(n))
    time.sleep(0.8)
    assert len(commits(nu)) == 2, "dentro do intervalo nada sai"
    assert json.loads((repo / ARQUIVO).read_text(encoding="utf-8")) == dados(4), "o arquivo local já mudou"
    ate(lambda: len(commits(nu)) == 3)
    assert publicado(nu) == dados(4)
    time.sleep(2.5)
    assert len(commits(nu)) == 3


def test_conteudo_igual_ao_do_remoto_nao_gera_commit(remoto, abre):
    nu, repo = remoto
    p = abre(repo, intervalo=0)
    p.notifica(dados(1))
    ate(lambda: len(commits(nu)) == 2)
    p.notifica(dados(1))
    assert p.encerra(timeout=10)
    assert len(commits(nu)) == 2


def outro_clone_empurra(tmp_path, nu, arquivo, conteudo):
    outro = tmp_path / "outro"
    if not outro.exists():
        subprocess.run(["git", "clone", "-q", str(nu), str(outro)], check=True, capture_output=True)
    git(outro, "pull", "-q", "origin", "main")
    (outro / arquivo).write_text(conteudo, encoding="utf-8")
    git(outro, "commit", "-q", "-am", "mudança de outra pessoa")
    git(outro, "push", "-q", "origin", "HEAD:main")


def test_remoto_que_andou_mantem_o_commit_alheio_e_recebe_o_painel_por_cima(tmp_path, remoto, abre):
    nu, repo = remoto
    outro_clone_empurra(tmp_path, nu, "README.md", "editado por outra pessoa\n")
    p = abre(repo, intervalo=0)
    p.notifica(dados(1))
    ate(lambda: len(commits(nu)) == 3)
    assert commits(nu)[1] == "mudança de outra pessoa"
    assert git(nu, "show", "main:README.md") == "editado por outra pessoa"
    assert publicado(nu) == dados(1)
    outro_clone_empurra(tmp_path, nu, ARQUIVO, "{}\n")  # alguém mexeu no próprio arquivo de dados
    p.notifica(dados(2))
    ate(lambda: len(commits(nu)) == 5)
    assert publicado(nu) == dados(2)


def test_falha_de_push_aparece_e_a_ultima_mudanca_sai_quando_o_remoto_volta(tmp_path, remoto, abre):
    nu, repo = remoto
    erros = []
    git(repo, "remote", "set-url", "origin", str(tmp_path / "fora-do-ar.git"))
    p = abre(repo, intervalo=0, espera_erro=0.3, ao_erro=erros.append)
    p.notifica(dados(1))
    ate(lambda: erros)
    assert "falha ao publicar" in erros[0]
    p.notifica(dados(2))
    time.sleep(1)
    assert len(erros) == 1, "a mesma falha não enche a tela a cada tentativa"
    git(repo, "remote", "set-url", "origin", str(nu))
    ate(lambda: len(commits(nu)) == 2)
    assert publicado(nu) == dados(2)
    ate(lambda: len(erros) == 2)
    assert "voltou a funcionar" in erros[1]


def test_situacao_diz_o_ultimo_envio_e_a_falha_em_curso_para_a_tela(tmp_path, remoto, abre):
    nu, repo = remoto
    p = abre(repo, intervalo=0, espera_erro=0.3, ao_erro=lambda m: None)
    assert p.situacao() == {"ultimo_envio_em": None, "ultimo_commit": None, "pendente": False, "falhas": 0,
                            "ultimo_erro": None, "ultimo_erro_em": None}
    p.notifica(dados(1))
    ate(lambda: len(commits(nu)) == 2)
    ok = ate(lambda: (s := p.situacao())["ultimo_commit"] and s)
    assert ok["ultimo_commit"] == git(nu, "rev-parse", "main") and ok["ultimo_envio_em"]
    assert (ok["pendente"], ok["falhas"], ok["ultimo_erro"]) == (False, 0, None)

    git(repo, "remote", "set-url", "origin", str(tmp_path / "fora-do-ar.git"))
    p.notifica(dados(2))
    falha = ate(lambda: (s := p.situacao())["falhas"] and s)
    assert falha["pendente"] is True and "git" in falha["ultimo_erro"] and falha["ultimo_erro_em"]
    assert falha["ultimo_commit"] == ok["ultimo_commit"]  # o último que chegou ao site continua valendo

    git(repo, "remote", "set-url", "origin", str(nu))
    voltou = ate(lambda: (s := p.situacao())["ultimo_commit"] != ok["ultimo_commit"] and s)
    assert (voltou["pendente"], voltou["falhas"], voltou["ultimo_erro"]) == (False, 0, None)


def test_encerrar_envia_o_que_esperava_o_intervalo(remoto, abre):
    nu, repo = remoto
    p = abre(repo, intervalo=600)
    p.notifica(dados(1))
    ate(lambda: len(commits(nu)) == 2)
    p.notifica(dados(2))
    assert p.encerra(timeout=30)
    assert len(commits(nu)) == 3
    assert publicado(nu) == dados(2)


def test_encerrar_sem_remoto_avisa_e_nao_trava(tmp_path, remoto, abre):
    _, repo = remoto
    erros = []
    git(repo, "remote", "set-url", "origin", str(tmp_path / "fora-do-ar.git"))
    p = abre(repo, intervalo=600, espera_erro=600, ao_erro=erros.append)
    p.notifica(dados(1))
    ate(lambda: erros)
    inicio = time.monotonic()
    assert p.encerra(timeout=30) is False
    assert time.monotonic() - inicio < 10


def test_pasta_em_outro_ramo_e_com_mudancas_fica_como_estava(remoto, abre):
    nu, repo = remoto
    git(repo, "checkout", "-q", "-b", "trabalho")
    (repo / "README.md").write_text("rascunho não commitado\n", encoding="utf-8")
    git(repo, "add", "README.md")
    antes = git(repo, "rev-parse", "HEAD")
    p = abre(repo, intervalo=0)
    p.notifica(dados(3))
    ate(lambda: len(commits(nu)) == 2)
    assert git(nu, "show", "--format=", "--name-only", "main") == ARQUIVO
    assert git(repo, "rev-parse", "HEAD") == antes
    assert git(repo, "branch", "--show-current") == "trabalho"
    assert git(repo, "diff", "--cached", "--name-only") == "README.md"
    assert git(nu, "show", "main:README.md") == "oi"


def test_push_recusado_por_ramo_que_andou_e_refeito_e_avisado_se_insiste(tmp_path, remoto, abre):
    nu, repo = remoto
    velho = tmp_path / "copia-velha.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(nu), str(velho)], check=True, capture_output=True)
    outro_clone_empurra(tmp_path, nu, "README.md", "novo\n")
    git(repo, "remote", "set-url", "origin", str(velho))  # busca uma ponta velha...
    git(repo, "remote", "set-url", "--push", "origin", str(nu))  # ...e empurra para a de verdade
    erros = []
    p = abre(repo, intervalo=0, espera_erro=0.3, ao_erro=erros.append)
    p.notifica(dados(1))
    ate(lambda: erros)
    assert "mudou no remoto" in erros[0]
    git(repo, "remote", "set-url", "origin", str(nu))
    ate(lambda: len(commits(nu)) == 3)
    assert publicado(nu) == dados(1)
    assert git(nu, "show", "main:README.md") == "novo"
