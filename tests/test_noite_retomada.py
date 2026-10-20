"""Retomada de verdade: a noite (linha falsa acelerada, dublê lento do Claude, publicador num remoto local)
roda num processo que é morto no meio; o mesmo comando, rodado de novo na mesma pasta, termina a noite
sem perder estado, sem chamar o Claude de novo para o que já estava no registro e sem republicar."""
import collections
import json
import subprocess
import sys
import time
from pathlib import Path

from publicador import ARQUIVO
from test_publicador import git, remoto  # noqa: F401 - remoto é fixture

PROCESSO = Path(__file__).parent / "processo_noite.py"
sys.path.insert(0, str(Path(__file__).parent))
from processo_noite import falas_da_noite  # noqa: E402


def eventos(pasta):
    arq = pasta / "eventos.jsonl"
    if not arq.exists():
        return []
    linhas = arq.read_text(encoding="utf-8").split("\n")
    return [json.loads(linha) for linha in linhas[:-1] if linha.strip()]


def roda(pasta, repo):
    return subprocess.Popen([sys.executable, "-P", str(PROCESSO), str(pasta), str(repo)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_matar_o_processo_no_meio_e_rodar_de_novo_nao_perde_estado_nem_republica(tmp_path, remoto):
    nu, repo = remoto
    pasta = tmp_path / "sessao"

    primeiro = roda(pasta, repo)
    limite = time.monotonic() + 180
    while sum(e["tipo"] == "publicacao_automatica" for e in eventos(pasta)) < 3:  # morre no meio da noite
        assert primeiro.poll() is None, primeiro.communicate()[1].decode("utf-8", "replace")
        assert time.monotonic() < limite, "o processo não chegou ao meio da noite"
        time.sleep(0.05)
    primeiro.kill()
    primeiro.wait()
    antes = eventos(pasta)
    analisadas_antes = {e["fala_id"] for e in antes if e["tipo"] == "afirmacoes"}
    chamadas_antes = (tmp_path / "chamadas.jsonl").read_text(encoding="utf-8").splitlines()
    assert 3 <= len([e for e in antes if e["tipo"] == "fala"]) < len(falas_da_noite())  # morreu no meio mesmo

    segundo = roda(pasta, repo)
    saida, erro = segundo.communicate(timeout=300)
    assert segundo.returncode == 0, erro.decode("utf-8", "replace")

    depois = eventos(pasta)
    assert depois[:len(antes)] == antes  # o registro só cresceu
    # cada fala da noite entrou na sessão uma vez só
    falas = collections.Counter(e["fala"]["texto"] for e in depois if e["tipo"] == "fala")
    assert falas == collections.Counter(f.texto for f in falas_da_noite())
    # o Claude não foi chamado de novo para fala já analisada antes do travamento
    chamadas_depois = [json.loads(c) for c in (tmp_path / "chamadas.jsonl").read_text(encoding="utf-8").splitlines()]
    novas = chamadas_depois[len(chamadas_antes):]
    assert not [c for c in novas if c["etapa"].removeprefix("extrair_") in analisadas_antes]
    # cada ligação publicada uma vez só, e o site tem o mesmo que a sessão
    publicadas = collections.Counter(e["ligacao_id"] for e in depois if e["tipo"] == "publicacao_automatica")
    candidatas = [f for f in falas_da_noite() if f.quem != "Mediação"]
    assert len(publicadas) == len(candidatas) and set(publicadas.values()) == {1}
    painel = json.loads((pasta / "painel.json").read_text(encoding="utf-8"))
    no_site = json.loads(git(nu, "show", f"main:{ARQUIVO}"))
    assert no_site["ligacoes"] == painel["ligacoes"]
    assert sorted(lig["minutagem_s"] for lig in painel["ligacoes"]) == [f.inicio_s for f in candidatas]
