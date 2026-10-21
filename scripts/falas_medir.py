"""Mede a linha do áudio às falas (falas.py) com o áudio do debate da Band, contra a transcrição de
referência do Aos Fatos (Escriba; local, fora do git).

    python -P scripts/falas_medir.py rodar NOME --inicio S --fim S [--ritmo 1|0]   # grava <dados>/falas/NOME.jsonl
    python -P scripts/falas_medir.py medir NOME [NOME ...]                          # imprime as métricas em markdown

Métricas, na região que a referência e a saída cobrem:
- divergência de palavras (como no avaliar.py: substituições + apagadas + inseridas sobre as palavras da
  referência; a referência tira repetições e muletas, então nunca chega a zero) e a fração das palavras da
  referência que aparecem na saída; a transcrição em lote do mesmo trecho (transcribe.py) é o termo de
  comparação;
- acerto de quem fala: nas palavras iguais na saída e na referência, a fração em que a fala tem o mesmo
  locutor que a referência;
- falas de candidato sem fala de referência no intervalo (ex.: propaganda no intervalo atribuída a um
  candidato);
- atraso entre o fim da fala no áudio e a entrega (só no ritmo real) e custo de processamento.
"""
import argparse
import difflib
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import vozes_medir as vozes  # noqa: E402
from comum import pasta_dados  # noqa: E402
from falas import FonteArquivo, LinhaDeFalas  # noqa: E402

ZERO_BAND = 15581.2  # abertura do mediador na gravação (1ª palavra do Escriba, 274,98 s, + 15306,2 s)
CLASSES = ("Lula", "Flávio", "Mediação")


def rodar(dados, nome, inicio, fim, ritmo, atribuicao="viterbi"):
    saida = dados / "falas" / f"{nome}.jsonl"
    saida.parent.mkdir(parents=True, exist_ok=True)
    if saida.exists():
        saida.unlink()
    fonte = FonteArquivo(dados / "raw" / "debate_audio.m4a", inicio, fim, ritmo or None)
    linha = LinhaDeFalas(fonte, zero=ZERO_BAND, registro=saida, atribuicao=atribuicao)
    linha._log({"evento": "rodada", "inicio": inicio, "fim": fim, "ritmo": ritmo, "zero": ZERO_BAND,
                "atribuicao": atribuicao})
    t0 = time.monotonic()
    n = 0
    for f in linha:
        n += 1
        print(f"{f.inicio_s:8.1f} {f.quem:9} {f.atraso_s if f.atraso_s is not None else '-':>6} {f.texto[:90]}",
              flush=True)
    linha._log({"evento": "fim_rodada", "parede_s": round(time.monotonic() - t0, 1), "falas": n,
                **linha.estatisticas})


def le_registro(path):
    evs = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    por = {}
    for e in evs:
        por.setdefault(e["evento"], []).append(e)
    return por


def referencia(dados, inicio, fim):
    """Palavras do Escriba no trecho: (segundo da gravação, palavra normalizada, locutor)."""
    trans = [p for p in vozes.transcricoes_alinhamento(dados, dados / "vozes") if p.exists()]
    toks = vozes.tokens_escriba(dados / "referencia" / "escriba_aosfatos.json")
    ta, da = vozes.deslocamento_escriba(toks, trans)
    out = []
    for s, e, txt, q in toks:
        t = s + float(np.interp(s, ta, da))
        if inicio <= t <= fim and vozes._norm(txt):
            out.append((t, vozes._norm(txt), q, e + float(np.interp(s, ta, da))))
    return sorted(out)


def lote_whisper(dados, inicio, fim):
    """Palavras da transcrição em lote (transcribe.py, large-v3-turbo) no trecho, como termo de comparação."""
    ws, ultimo = [], -1.0
    for p in vozes.transcricoes_alinhamento(dados, dados / "vozes"):
        if not p.exists():
            continue
        for s in json.loads(p.read_text(encoding="utf-8"))["segments"]:
            for w in s["words"]:
                if w["s"] > ultimo and inicio <= w["s"] <= fim and vozes._norm(w["w"]):
                    ws.append((vozes._norm(w["w"]), None))
        ultimo = max([ultimo] + [s["end"] for s in json.loads(p.read_text(encoding="utf-8"))["segments"]])
    return ws


def compara(ref, hip):
    """ref: [(palavra, locutor)], hip: [(palavra, locutor)]. Devolve divergência, cobertura e pares de locutor."""
    sm = difflib.SequenceMatcher(None, [x[0] for x in ref], [x[0] for x in hip], autojunk=False)
    ops = sm.get_opcodes()
    eq = [o for o in ops if o[0] == "equal" and o[2] - o[1] >= 5]
    (i0, j0), (i1, j1) = (eq[0][1], eq[0][3]), (eq[-1][2], eq[-1][4])
    erros, iguais, pares = 0, 0, Counter()
    for tag, a0, a1, b0, b1 in ops:
        if a1 <= i0 or a0 >= i1:
            continue
        if tag == "equal":
            iguais += a1 - a0
            for i, j in zip(range(a0, a1), range(b0, b1)):
                if hip[j][1]:
                    pares[(ref[i][1], hip[j][1])] += 1
        else:
            erros += max(a1 - a0, b1 - b0)
    return {"divergencia": erros / (i1 - i0), "cobertura": iguais / (i1 - i0), "n_ref": i1 - i0,
            "n_hip": j1 - j0, "pares": pares}


def pct(x):
    return f"{100 * x:.1f}%"


def medir(dados, nomes, janela=None):
    """janela=(a, b): mede só as falas que começam em [a, b) s da gravação, contra a referência de [a, b)."""
    linhas = []
    w = linhas.append
    for nome in nomes:
        reg = le_registro(dados / "falas" / f"{nome}.jsonl")
        rod = reg["rodada"][0]
        ini, fim, ritmo, zero = rod["inicio"], rod["fim"], rod["ritmo"], rod["zero"]
        falas = reg.get("fala", [])
        lotes = reg.get("lote", [])
        if janela:
            falas = [f for f in falas if janela[0] <= f["inicio_s"] + zero < janela[1]]
            ini, fim = max(ini, janela[0]), min(fim, janela[1])
        ref_full = referencia(dados, ini, fim)
        ref = [(p, q) for _, p, q, _ in ref_full]
        hip = [(vozes._norm(x), f["quem"]) for f in falas for x in f["texto"].split() if vozes._norm(x)]
        r = compara(ref, hip)
        b = compara(ref, lote_whisper(dados, ini, fim))
        pares = r["pares"]
        tot = sum(pares.values())
        certo = sum(n for (a, h), n in pares.items() if a == h)
        if "fim_rodada" not in reg:
            raise SystemExit(f"{nome}: a rodada não terminou (sem evento fim_rodada)")
        fr = reg["fim_rodada"][-1]
        cpu = sum(x["whisper_s"] + x["voz_s"] for x in lotes)
        dur = fim - ini
        w(f"### {nome}: {ini:.0f}–{fim:.0f} s da gravação ({dur / 60:.1f} min), "
          f"{'ritmo real' if ritmo == 1 else 'acelerado'}\n")
        w(f"- Falas entregues: {len(falas)} ({Counter(f['quem'] for f in falas)}); "
          f"duração média {np.mean([f['fim_s'] - f['inicio_s'] for f in falas]):.1f} s.")
        w(f"- **Texto:** divergência de palavras {pct(r['divergencia'])} (transcrição em lote do mesmo trecho: "
          f"{pct(b['divergencia'])}); palavras da referência presentes na saída {pct(r['cobertura'])} "
          f"(lote: {pct(b['cobertura'])}). Referência: {r['n_ref']} palavras.")
        w(f"- **Quem fala:** {pct(certo / tot)} das {tot} palavras iguais com o locutor certo.")
        # falas de candidato sem nenhuma palavra de referência no intervalo delas
        tref = np.array([t for t, *_ in ref_full])
        orfas = [f for f in falas if f["quem"] in ("Lula", "Flávio")
                 and not ((tref >= f["inicio_s"] + zero - 1) & (tref <= f["fim_s"] + zero + 1)).any()]
        w(f"- Falas de candidato sem nenhuma palavra da referência no intervalo (a referência pula algumas falas "
          f"sobrepostas; ver a lista): {len(orfas)}"
          + (": " + "; ".join(f"{f['inicio_s']:.0f} s {f['quem']} «{f['texto'][:60]}»" for f in orfas[:6])
             if orfas else "") + ".")
        atr = [f["atraso_s"] for f in falas if f.get("atraso_s") is not None]
        if atr:
            a = np.array(atr)
            w(f"- **Atraso** do fim da fala à entrega: mediana {np.median(a):.1f} s, p90 "
              f"{np.percentile(a, 90):.1f} s, máximo {a.max():.1f} s. Atraso do transcritor (fim do áudio do "
              f"lote → lote pronto): mediana {np.median([x['atraso_s'] for x in lotes]):.1f} s, máximo "
              f"{max(x['atraso_s'] for x in lotes):.1f} s.")
        w(f"- **Processamento:** {len(lotes)} chamadas ao Whisper, mediana {np.median([x['whisper_s'] for x in lotes]):.1f} s "
          f"cada (com {np.median([x['audio_s'] for x in lotes]):.1f} s de fala); Whisper + voz = {cpu:.0f} s para "
          f"{dur:.0f} s de áudio (fator {cpu / dur:.2f} do tempo real)"
          + (f"; tempo de parede {fr['parede_s']:.0f} s" if fr.get("parede_s") else "") + ".")
        w("")
        w("| referência ↓ / saída → | " + " | ".join(CLASSES) + " | acerto |")
        w("|---|" + "---:|" * (len(CLASSES) + 1))
        for c in CLASSES:
            n = sum(pares[(c, h)] for h in CLASSES)
            if n:
                w(f"| {c} | " + " | ".join(str(pares[(c, h)]) for h in CLASSES) + f" | {pct(pares[(c, c)] / n)} |")
        w("")
    print("\n".join(linhas))
    return "\n".join(linhas)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dados", type=Path, help="pasta de dados (padrão: CHECAGEM_DADOS ou data/ do checkout principal)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rodar")
    r.add_argument("nome")
    r.add_argument("--inicio", type=float, required=True)
    r.add_argument("--fim", type=float, required=True)
    r.add_argument("--ritmo", type=float, default=1.0)
    r.add_argument("--atribuicao", default="viterbi", choices=("janelas", "viterbi"))
    m = sub.add_parser("medir")
    m.add_argument("nomes", nargs="+")
    m.add_argument("--janela", type=float, nargs=2, help="mede só [A, B) s da gravação")
    a = ap.parse_args()
    a.dados = pasta_dados(a.dados)
    if a.cmd == "rodar":
        rodar(a.dados, a.nome, a.inicio, a.fim, a.ritmo, a.atribuicao)
    else:
        medir(a.dados, a.nomes, a.janela)


if __name__ == "__main__":
    main()
