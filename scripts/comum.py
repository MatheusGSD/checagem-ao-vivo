"""O que os scripts da checagem ao vivo têm em comum: o fuso de Brasília, a pasta de dados e a leitura
dos registros JSONL (uma linha JSON por evento, fala ou página bruta). Só biblioteca padrão."""
import json
import os
import subprocess
from datetime import timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BRASILIA = timezone(timedelta(hours=-3))


def raiz_principal():
    """Raiz do checkout principal (num worktree, data/ e .env só existem lá)."""
    try:
        comum = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=RAIZ,
                               capture_output=True, text=True, check=True).stdout.strip()
        return Path(comum).parent
    except (OSError, subprocess.CalledProcessError):
        return RAIZ


def pasta_dados(dados=None):
    """A pasta de dados: `dados` (--dados), a variável CHECAGEM_DADOS ou data/ do checkout principal."""
    return Path(dados or os.environ.get("CHECAGEM_DADOS") or raiz_principal() / "data")


def le_jsonl(arquivo, corta_incompleta=False):
    """As linhas JSON de um arquivo (vazio se ele não existe). Uma última linha sem o \\n final é uma
    gravação interrompida: entra se for JSON completo, senão fica de fora. Com `corta_incompleta`, ela
    é cortada do arquivo, para um registro só de acréscimo seguir gravando em linha nova."""
    arquivo = Path(arquivo)
    if not arquivo.exists():
        return []
    bruto = arquivo.read_bytes()
    *linhas, resto = bruto.split(b"\n")
    out = [json.loads(linha) for linha in linhas if linha.strip()]
    if resto.strip():
        if corta_incompleta:
            with arquivo.open("r+b") as f:
                f.truncate(len(bruto) - len(resto))
        else:
            try:
                out.append(json.loads(resto))
            except ValueError:
                pass
    return out
