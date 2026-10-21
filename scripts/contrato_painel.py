"""Confere o arquivo de dados da página da Globo (docs/globo/data/painel.json) contra o contrato.

Uso:
  python contrato_painel.py ARQUIVO.json

O contrato está descrito campo a campo em docs/globo/formato-dos-dados.md. A sessão de checagem
chama `valida` antes de gravar o arquivo; a lista devolvida tem uma mensagem por problema, com o
caminho do campo, e fica vazia quando o arquivo está certo.
"""
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

VERSAO = 1


# tipos de campo: cada um devolve a mensagem de erro, ou None se o valor serve
def texto(v):
    return None if isinstance(v, str) and v.strip() else "deve ser texto não vazio"


def booleano(v):
    return None if isinstance(v, bool) else "deve ser true ou false"


def instante(v):
    """Data e hora ISO 8601 com fuso, ex. 2026-10-23T21:20:00-03:00."""
    try:
        if datetime.fromisoformat(v).tzinfo is not None:
            return None
    except (TypeError, ValueError):
        pass
    return "deve ser data e hora ISO 8601 com fuso, ex. 2026-10-23T21:20:00-03:00"


def nota(v):
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1
    return None if ok else "deve ser um número entre 0 e 1"


def segundos(v):
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
    return None if ok else "deve ser um número de segundos desde o 00:00, maior ou igual a 0"


def um_de(*valores):
    return lambda v: None if v in valores else f"deve ser um de: {', '.join(map(str, valores))}"


def dia(v):
    """Data do dia no formato 2026-02-11."""
    try:
        if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) and date.fromisoformat(v):
            return None
    except ValueError:
        pass
    return "deve ser data no formato AAAA-MM-DD"


def ou_nulo(tipo):
    return lambda v: None if v is None else tipo(v)


def lista_de(campos, minimo=0):
    return ("lista", campos, minimo)


def variantes(campo, por_valor):
    """Objeto de mais de uma forma: o valor de `campo` escolhe os campos (por_valor[valor])."""
    return ("variantes", campo, por_valor)


META = {
    "titulo": texto,
    "debate": texto,
    "inicio_debate": ou_nulo(instante),
    "atualizado_em": instante,
    "exemplo": booleano,
    "encerrado": booleano,
    "fontes": lista_de({"nome": texto, "url": texto}),
    "agencias_incluidas": lista_de({"nome": texto, "via": texto}),
    "agencias_excluidas": lista_de({"nome": texto, "motivo": texto}),
    "modelo": texto,  # o modelo pedido
    "modelos_que_responderam": lista_de(texto),  # os que de fato responderam (fallback da API), sem repetição
    "prompts": lista_de({"arquivo": texto, "sha256": texto}),
    "limiar_automatico": ou_nulo(nota),  # null = publicação automática desligada
}
# selo conferido pelo operador na página da agência, ou rótulo como veio nos dados da agência
FONTE_ROTULO = um_de("selo_da_pagina", "dados_da_agencia")
CHECAGEM = {
    "agencia": texto,
    "data": ou_nulo(dia),  # algumas checagens vêm sem data
    "rotulo": texto,
    "fonte_rotulo": FONTE_ROTULO,
    "origem": um_de("aprovada", "automatica"),  # como esta checagem entrou na ligação
    "url": texto,
}
LIGACAO = {
    "id": texto,
    "minutagem_s": segundos,
    "quem": um_de("Lula", "Flávio"),
    "trecho": texto,
    "reformulacao": texto,
    "tipo": um_de("numero", "perene"),
    "origem": um_de("aprovada", "automatica"),  # como a ligação entrou no painel (a 1ª publicação)
    "publicada_em": instante,
    "ligada_depois": booleano,
    "checagens": lista_de(CHECAGEM, minimo=1),
}
SEM_PAR = {
    "minutagem_s": segundos,
    "quem": um_de("Lula", "Flávio"),
    "trecho": texto,
    "reformulacao": texto,
}
DESPUBLICACAO = {
    "tipo": um_de("despublicacao"),
    "em": instante,
    "ligacao_id": texto,
    "motivo": texto,
    "retirado": LIGACAO,  # a ligação como estava publicada
}
CORRECAO_ROTULO = {  # o rótulo exibido de uma checagem de ligação publicada foi trocado
    "tipo": um_de("rotulo"),
    "em": instante,
    "ligacao_id": texto,
    "minutagem_s": segundos,
    "quem": um_de("Lula", "Flávio"),
    "agencia": texto,
    "url": texto,  # a checagem
    "rotulo_anterior": texto,
    "fonte_rotulo_anterior": FONTE_ROTULO,
    "rotulo": texto,
    "fonte_rotulo": FONTE_ROTULO,
}
CORRECAO = variantes("tipo", {"despublicacao": DESPUBLICACAO, "rotulo": CORRECAO_ROTULO})
RAIZ = {
    "versao_contrato": um_de(VERSAO),
    "meta": META,
    "ligacoes": lista_de(LIGACAO),
    "sem_par": lista_de(SEM_PAR),
    "correcoes": lista_de(CORRECAO),
}


def valida(dados):
    erros = []

    def erro(caminho, msg):
        erros.append(f"{caminho}: {msg}")

    def confere(valor, tipo, caminho):
        if isinstance(tipo, dict):  # objeto: campos obrigatórios, nenhum a mais
            if not isinstance(valor, dict):
                return erro(caminho or "raiz", "deve ser um objeto")
            for nome, sub in tipo.items():
                c = f"{caminho}.{nome}" if caminho else nome
                if nome not in valor:
                    erro(c, "campo obrigatório ausente")
                else:
                    confere(valor[nome], sub, c)
            for nome in sorted(valor.keys() - tipo.keys()):
                erro(f"{caminho}.{nome}" if caminho else nome, "campo fora do contrato")
        elif isinstance(tipo, tuple) and tipo[0] == "variantes":
            if not isinstance(valor, dict):
                return erro(caminho, "deve ser um objeto")
            if (forma := tipo[2].get(valor.get(tipo[1]))) is None:
                return erro(f"{caminho}.{tipo[1]}", f"deve ser um de: {', '.join(tipo[2])}")
            confere(valor, forma, caminho)
        elif isinstance(tipo, tuple):  # lista_de(...)
            if not isinstance(valor, list):
                return erro(caminho, "deve ser uma lista")
            if len(valor) < tipo[2]:
                erro(caminho, f"deve ter pelo menos {tipo[2]} item")
            for i, item in enumerate(valor):
                confere(item, tipo[1], f"{caminho}[{i}]")
        elif (msg := tipo(valor)) is not None:
            erro(caminho, msg)

    confere(dados, RAIZ, "")
    if isinstance(dados, dict) and isinstance(dados.get("ligacoes"), list):
        vistos = set()
        for i, lig in enumerate(dados["ligacoes"]):
            ident = lig.get("id") if isinstance(lig, dict) else None
            if ident in vistos:
                erro(f"ligacoes[{i}].id", f"identificador repetido: {ident}")
            vistos.add(ident)
    return erros


if __name__ == "__main__":
    problemas = valida(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    for p in problemas:
        print(p)
    sys.exit(1 if problemas else 0)
