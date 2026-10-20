"""Agente de interpretação: chama a API do Claude com prompts fixos, publicados em prompts/.

Uso:
  python agente.py rotulos TRANSCRICAO.json ROTULOS_AUTO.json SAIDA_CORRECOES.json
  python agente.py classificar TURNOS.json SAIDA_CLASSIFICACAO.json

`rotulos` revisa quem disse cada segmento e os nomes que o Whisper errou; a saída tem o formato de
bloco*_correcoes.json. `classificar` dá tipo, temas e resumo a cada fala, extrai afirmações
checáveis e fios de perguntas diretas; a saída tem o formato de bloco*_classificacao.json.
Toda saída é validada (trechos literais, temas da lista fixa, nenhuma fala sem classificação) e
cada chamada fica registrada em data/agente/ com entrada, saída, modelo e uso de tokens.
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import os

import anthropic

RAIZ = Path(__file__).resolve().parent.parent
# a chave pode vir do ambiente ou do .env na raiz do projeto (ignorado pelo git)
if not os.environ.get("ANTHROPIC_API_KEY") and (RAIZ / ".env").exists():
    for linha in (RAIZ / ".env").read_text(encoding="utf-8").splitlines():
        nome, _, valor = linha.partition("=")
        if nome.strip() == "ANTHROPIC_API_KEY" and valor.strip():
            os.environ["ANTHROPIC_API_KEY"] = valor.strip()
PROMPTS = RAIZ / "prompts"
LOGS = RAIZ / "data" / "agente"
MODELO = "claude-opus-5-5"
ESFORCO = "high"
TIPOS = ["ataque", "defesa", "proposta", "outro"]
TEMAS = ["Caso Vorcaro", "Fraude no INSS", "Lulinha e Luchsinger", "Família Bolsonaro", "Mentira e credibilidade",
         "Corrupção em geral", "Segurança e armas", "Economia e dívidas", "Bolsa Família", "PF e Judiciário",
         "Política externa", "Mulheres", "Escala 6x1", "Inclusão e educação", "Eleição e votos", "Bate-boca", "Outro"]
NOME = {"L": "Lula", "F": "Flávio", "M": "Mediação"}


def objeto(props, **kw):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False, **kw}


def lista(item):
    return {"type": "array", "items": item}


TXT, INT = {"type": "string"}, {"type": "integer"}


class RespostaInvalida(SystemExit):
    """O modelo recusou ou a resposta foi cortada. Na linha de comando encerra o script, como antes;
    a sessão de checagem ao vivo a captura para não cair."""


def chama(etapa, prompt, conteudo, schema, logs=None):
    """Uma chamada com saída estruturada. Registra tudo em `logs` (padrão data/agente/) e devolve
    (saída, registro)."""
    system = (PROMPTS / prompt).read_text(encoding="utf-8")
    with anthropic.Anthropic().beta.messages.stream(
        model=MODELO,
        max_tokens=64000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # se o modelo recusar, a API refaz no modelo de reserva
        thinking={"type": "adaptive"},
        output_config={"effort": ESFORCO, "format": {"type": "json_schema", "schema": schema}},
        system=system,
        messages=[{"role": "user", "content": conteudo}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RespostaInvalida(f"{etapa}: o modelo recusou ({msg.stop_details})")
    if msg.stop_reason == "max_tokens":
        raise RespostaInvalida(f"{etapa}: a resposta foi cortada no limite de tokens")
    saida = json.loads(next(b.text for b in msg.content if b.type == "text"))
    registro = {
        "etapa": etapa, "data": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "modelo_pedido": MODELO, "modelo_respondeu": msg.model, "esforco": ESFORCO,
        "prompt": f"prompts/{prompt}", "prompt_sha256": hashlib.sha256(system.encode()).hexdigest(),
        "entrada_sha256": hashlib.sha256(conteudo.encode()).hexdigest(),
        "tokens": {"entrada": msg.usage.input_tokens, "saida": msg.usage.output_tokens},
    }
    pasta = Path(logs or LOGS)
    pasta.mkdir(parents=True, exist_ok=True)
    texto = json.dumps({**registro, "entrada": conteudo, "saida": saida}, ensure_ascii=False, indent=1)
    base, n = f"{etapa}_{registro['data'].replace(':', '')}", 1
    while True:  # duas chamadas da mesma etapa no mesmo segundo não se sobrescrevem
        nome = f"{base}.json" if n == 1 else f"{base}_{n}.json"
        try:
            with (pasta / nome).open("x", encoding="utf-8") as f:
                f.write(texto)
            break
        except FileExistsError:
            n += 1
    print(f"{etapa}: {msg.model}, {registro['tokens']['entrada']} tokens de entrada, "
          f"{registro['tokens']['saida']} de saída -> {pasta.name}/{nome}")
    return saida, registro


def rotulos(trans_path, auto_path, saida_path):
    segs = json.loads(Path(trans_path).read_text(encoding="utf-8"))["segments"]
    auto = json.loads(Path(auto_path).read_text(encoding="utf-8"))
    conteudo = "Transcrição (segmento | segundo | rótulo automático | texto):\n" + "\n".join(
        f"{i} | {s['start']:.1f} | {auto[i]} | {s['text']}" for i, s in enumerate(segs))
    schema = objeto({
        "correcoes": lista(objeto({"seg": INT, "quem": {"type": "string", "enum": ["L", "F", "M"]}, "motivo": TXT})),
        "nomes": lista(objeto({"errado": TXT, "certo": TXT, "motivo": TXT})),
    })
    out, reg = chama("rotulos", "rotulos.md", conteudo, schema)
    texto_todo = "\n".join(s["text"] for s in segs)
    erros = [f"segmento {c['seg']} não existe" for c in out["correcoes"] if not 0 <= c["seg"] < len(segs)]
    erros += [f"nome {n['errado']!r} não aparece na transcrição" for n in out["nomes"] if n["errado"] not in texto_todo]
    if erros:
        raise SystemExit("rotulos: saída inválida:\n" + "\n".join(erros))

    def regra(errado):  # trecho literal -> regex com borda de palavra onde o trecho começa/termina em letra
        return (r"\b" if errado[:1].isalnum() else "") + re.escape(errado) + (r"\b" if errado[-1:].isalnum() else "")

    Path(saida_path).write_text(json.dumps({
        "fonte": "agente (prompts/rotulos.md)", "agente": reg,
        "rotulos_manuais": {str(c["seg"]): c["quem"] for c in out["correcoes"] if c["quem"] != auto[c["seg"]]},
        "motivos": {str(c["seg"]): c["motivo"] for c in out["correcoes"]},
        "normalizacao": [[regra(n["errado"]), n["certo"]] for n in out["nomes"]],
        "motivos_nomes": {n["errado"]: n["motivo"] for n in out["nomes"]},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"rotulos: {len(out['correcoes'])} correções de quem fala, {len(out['nomes'])} nomes -> {saida_path}")


def classificar(turnos_path, saida_path):
    turns = json.loads(Path(turnos_path).read_text(encoding="utf-8"))["turns"]
    t0 = turns[0]["start"]
    cands = [i for i, t in enumerate(turns) if t["speaker"] != "Mediação"]
    conteudo = (f"Temas permitidos: {', '.join(TEMAS)}.\n\nFalas (número | segundo no bloco | quem | texto):\n" +
                "\n".join(f"{i} | {turns[i]['start'] - t0:.0f} | {turns[i]['speaker']} | {turns[i]['text']}" for i in cands))
    quem = {"type": "string", "enum": ["Lula", "Flávio"]}
    schema = objeto({
        "turnos": lista(objeto({"i": INT, "tipo": {"type": "string", "enum": TIPOS},
                                "temas": lista({"type": "string", "enum": TEMAS}), "resumo": TXT})),
        "afirmacoes": lista(objeto({"i": INT, "trecho": TXT, "texto": TXT})),
        "fios_perguntas": lista(objeto({"quem": quem, "tema": TXT,
                                        "instancias": lista(objeto({"i": INT, "trecho": TXT}))})),
    })
    out, reg = chama("classificar", "classificar.md", conteudo, schema)

    erros = []
    vistos = [x["i"] for x in out["turnos"]]
    if sorted(vistos) != cands:
        erros.append(f"falas sem classificação: {sorted(set(cands) - set(vistos))}; repetidas ou estranhas: "
                     f"{sorted(i for i in vistos if vistos.count(i) > 1 or i not in cands)}")
    erros += [f"fala {x['i']}: {len(x['temas'])} temas" for x in out["turnos"] if not 1 <= len(x["temas"]) <= 3]

    def literal(i, trecho, onde):
        if i not in cands:
            erros.append(f"{onde}: fala {i} não existe")
        elif trecho not in turns[i]["text"]:
            erros.append(f"{onde}: trecho não é literal na fala {i}: {trecho!r}")

    for a in out["afirmacoes"]:
        literal(a["i"], a["trecho"], "afirmação")
    for f in out["fios_perguntas"]:
        for it in f["instancias"]:
            literal(it["i"], it["trecho"], f"pergunta '{f['tema']}'")
            if it["i"] in cands and turns[it["i"]]["speaker"] != f["quem"]:
                erros.append(f"pergunta '{f['tema']}': a fala {it['i']} não é de {f['quem']}")
    if erros:
        raise SystemExit("classificar: saída inválida:\n" + "\n".join(erros))

    rel = lambda i: round(turns[i]["start"] - t0, 1)  # noqa: E731  (chave: início no bloco, ao décimo de segundo)
    por_i = {x["i"]: x for x in out["turnos"]}
    classe = {f"{rel(i):.1f}": ([por_i[i]["tipo"], por_i[i]["temas"], por_i[i]["resumo"]] if i in por_i
                            else [None, [], "Fala da mediação."]) for i in range(len(turns))}
    Path(saida_path).write_text(json.dumps({
        "meta": {"titulo": "Confronto direto (banco de tempo)", "relogio_min": 15,
                 "fonte": "transmissão ao vivo da Band no YouTube (18/10/2026). Classificação feita pelo agente "
                          f"({MODELO}, prompts/classificar.md) a partir da transcrição, sem revisão humana."},
        "agente": reg,
        "tipos": {"ataque": "acusação ou cobrança dirigida ao adversário", "defesa": "resposta a uma acusação",
                  "proposta": "promessa, plano ou realização própria", "outro": "aparte, provocação curta ou fala sem conteúdo próprio"},
        "turnos": classe,
        "afirmacoes": [{"t": rel(a["i"]), "quem": turns[a["i"]]["speaker"], "texto": a["texto"], "trecho": a["trecho"]}
                       for a in sorted(out["afirmacoes"], key=lambda a: a["i"])],
        "fios_perguntas": [{"quem": f["quem"], "tema": f["tema"], "instancias": [[rel(it["i"]), it["trecho"]] for it in f["instancias"]]}
                           for f in out["fios_perguntas"]],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"classificar: {len(out['turnos'])} falas, {len(out['afirmacoes'])} afirmações, "
          f"{len(out['fios_perguntas'])} fios de pergunta -> {saida_path}")


if __name__ == "__main__":
    etapa, *args = sys.argv[1:]
    {"rotulos": rotulos, "classificar": classificar}[etapa](*args)
