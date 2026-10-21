"""Perfis de voz e medição do reconhecimento de quem fala (vozes.py) no debate da Band.

`perfis` calcula os perfis de Lula e Flávio a partir das janelas de ~3 s do confronto livre, rotuladas
pela fala de referência (bloco1_final.json). `medir` classifica trechos do debate todo contra a
transcrição de referência do Aos Fatos (Escriba; local, fora do git), escolhe o limiar de "Mediação",
grava o limiar em perfis.json e escreve o relatório.

Linha de comando (pasta de dados: --dados, CHECAGEM_DADOS ou data/ do checkout principal):
    python -P scripts/vozes_medir.py perfis    # grava <dados>/vozes/perfis.json
    python -P scripts/vozes_medir.py medir     # mede no debate todo, escolhe o limiar, grava relatorio.md
                                               # (--reusar: refaz só o limiar e o relatório)
(`python -P scripts/vozes.py perfis|medir` faz o mesmo.)
"""
import argparse
import difflib
import json
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comum import pasta_dados  # noqa: E402
from vozes import (MEDIACAO, MINIMO_S, MODELO, TAXA, Reconhecedor, _unitario, codificador_ecapa,  # noqa: E402
                   gravar_perfis)


# ---------------------------------------------------------------- áudio e referências

def le_audio(audio, inicio, fim):
    """Trecho [inicio, fim] s do arquivo, em float32 mono 16 kHz (ffmpeg, como no transcribe.py)."""
    wav = Path(tempfile.gettempdir()) / f"vozes_{int(inicio)}_{int(fim)}.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(inicio), "-to", str(fim), "-i", str(audio),
                    "-ac", "1", "-ar", str(TAXA), str(wav)], check=True)
    with wave.open(str(wav)) as wf:
        sig = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    wav.unlink()
    return sig


def rotulo_por_cobertura(ini, fim, falas, minimo=0.9):
    """Locutor que cobre pelo menos `minimo` da janela [ini, fim]; None se a janela cruza troca de locutor."""
    cob = {}
    for f in falas:
        ov = min(fim, f["end"]) - max(ini, f["start"])
        if ov > 0:
            cob[f["speaker"]] = cob.get(f["speaker"], 0) + ov
    if not cob:
        return None
    quem = max(cob, key=cob.get)
    return quem if cob[quem] >= minimo * (fim - ini) else None


ESCRIBA_OFFSET = 15313.5  # segundo do Escriba -> segundo da gravação (o do avaliar.py; vale só no começo)
ESCRIBA_NOME = {"Lula": "Lula", "Flávio Bolsonaro": "Flávio", "Jornalista": MEDIACAO}


def _norm(w):
    w = unicodedata.normalize("NFKD", w.lower())
    return re.sub(r"[^\w]", "", "".join(c for c in w if not unicodedata.combining(c)))


def tokens_escriba(path):
    """Palavras do Escriba (início, fim, texto, locutor), em segundos do Escriba, ordenadas no tempo. Descarta
    parágrafos e palavras com tempo impossível (o arquivo tem alguns)."""
    toks, spk = [], None
    for p in json.loads(Path(path).read_text(encoding="utf-8")):
        spk = ESCRIBA_NOME.get(p.get("speaker"), spk) if p.get("speaker") else spk
        if not 0 < p["end"] - p["start"] < 150:
            continue
        for t in p["tokens"]:
            if 0 < t["end"] - t["start"] <= 2.5 and p["start"] - 1 <= t["start"] and t["end"] <= p["end"] + 1:
                toks.append((t["start"], t["end"], t["text"], spk))
    return sorted(toks)


def deslocamento_escriba(toks, transcricoes, minimo=4, suaviza=41):
    """O deslocamento fixo do avaliar.py (15313,5 s) erra o tempo por ~7 s, o que basta para trocar o locutor
    perto de cada troca de fala, e há desvios locais. Alinha as palavras do Escriba às do Whisper (difflib, como no avaliar.py) e devolve os pares
    (segundo do Escriba, deslocamento suavizado) das palavras casadas em sequências de >= `minimo`."""
    ws, fim = [], -1.0
    for path in transcricoes:  # em ordem; cada arquivo só contribui depois do fim do anterior
        segs = json.loads(Path(path).read_text(encoding="utf-8"))["segments"]
        novos = [(w["s"], _norm(w["w"])) for s in segs for w in s["words"] if w["s"] > fim and _norm(w["w"])]
        ws += novos
        fim = max(fim, novos[-1][0]) if novos else fim
    esc = [(t[0], _norm(t[2])) for t in toks if _norm(t[2])]
    sm = difflib.SequenceMatcher(None, [x[1] for x in esc], [x[1] for x in ws], autojunk=False)
    pares = [(esc[a + k][0], ws[b + k][0] - esc[a + k][0])
             for a, b, n in sm.get_matching_blocks() if n >= minimo for k in range(n)]
    t = np.array([p[0] for p in pares])
    d = np.array([p[1] for p in pares])
    h = suaviza // 2
    d = np.array([np.median(d[max(0, i - h): i + h + 1]) for i in range(len(d))])
    return t, d


def intervalos_escriba(path, transcricoes=(), junta=0.6):
    """Fala de referência do debate todo (Escriba/Aos Fatos) como intervalos de um só locutor, em segundos
    da gravação. Usa o tempo das palavras, corrigido pelo alinhamento com as transcrições do Whisper (sem
    elas, o deslocamento fixo do avaliar.py), e corta as sobreposições entre locutores diferentes."""
    brutos = tokens_escriba(path)
    if transcricoes:
        ta, da = deslocamento_escriba(brutos, transcricoes)
        desl = lambda s: float(np.interp(s, ta, da))  # noqa: E731
    else:
        desl = lambda s: ESCRIBA_OFFSET  # noqa: E731
    toks = sorted((s + desl(s), e + desl(s), q) for s, e, _, q in brutos)
    ivs = []
    for s, e, q in toks:
        if ivs and ivs[-1][2] == q and s - ivs[-1][1] <= junta:
            ivs[-1][1] = max(ivs[-1][1], e)
        else:
            ivs.append([s, e, q])
    # tira de cada intervalo o que se sobrepõe a intervalo de outro locutor
    limpos = []
    for i, (s, e, q) in enumerate(ivs):
        for j in range(max(0, i - 20), min(len(ivs), i + 20)):
            s2, e2, q2 = ivs[j]
            if q2 != q and s2 < e and s < e2:
                if s2 <= s:
                    s = max(s, e2)
                else:
                    e = min(e, s2)
        if e - s > 0:
            limpos.append({"start": s, "end": e, "speaker": q})
    return limpos


def janelas(intervalos, dur):
    """Corta cada intervalo em janelas de ~dur s, cobrindo o intervalo inteiro."""
    out = []
    for iv in intervalos:
        d = iv["end"] - iv["start"]
        if d < MINIMO_S:
            continue
        n = max(1, round(d / dur))
        for k in range(n):
            out.append((iv["start"] + k * d / n, iv["start"] + (k + 1) * d / n, iv["speaker"]))
    return out


# ---------------------------------------------------------------- comandos

def cmd_perfis(dados, saida):
    diar = json.loads((dados / "bloco1_diar.json").read_text(encoding="utf-8"))["chunks"]
    falas = json.loads((dados / "bloco1_final.json").read_text(encoding="utf-8"))["turns"]
    ini, fim = falas[0]["start"], falas[-1]["end"]
    sig = le_audio(dados / "raw" / "debate_audio.m4a", ini, fim)
    cod = codificador_ecapa()
    embs = {"Lula": [], "Flávio": []}
    for c in diar:
        quem = rotulo_por_cobertura(c["start"], c["end"], falas)
        if quem not in embs:
            continue  # mediação, janela entre dois locutores ou sem fala de referência
        a, b = int((c["start"] - ini) * TAXA), int((c["end"] - ini) * TAXA)
        if b - a < MINIMO_S * TAXA:
            continue
        embs[quem].append(_unitario(cod(sig[a:b])))
    perfis = {k: np.mean(v, axis=0) for k, v in embs.items()}
    anterior = saida / "perfis.json"
    limiar = json.loads(anterior.read_text(encoding="utf-8"))["limiar"] if anterior.exists() else None
    gravar_perfis(saida, perfis, limiar, {
        "fonte": "janelas de ~3 s do bloco1_diar.json (confronto livre da Band, 16226-18162 s de "
                 "raw/debate_audio.m4a), rotuladas pela fala de bloco1_final.json que cobre >= 90% da janela",
        "janelas": {k: len(v) for k, v in embs.items()},
        "segundos": {k: round(sum(c["end"] - c["start"] for c in diar
                                  if rotulo_por_cobertura(c["start"], c["end"], falas) == k), 1) for k in embs},
        "criado_em": datetime.now().isoformat(timespec="seconds")})
    print(f"perfis: { {k: len(v) for k, v in embs.items()} } janelas -> {saida / 'perfis.json'}")


BLOCO1 = (16226.0, 18162.0)  # confronto livre: de onde saem os perfis, fora da medição principal


def transcricoes_alinhamento(dados, saida):
    """Transcrições do Whisper usadas para alinhar o Escriba, em ordem. A primeira cobre o 1º bloco, que
    não tinha transcrição: python -P scripts/transcribe.py <dados>/raw/debate_audio.m4a 15300 16010
    <dados>/vozes/transcricao_inicio.json large-v3-turbo --vocab"""
    return [saida / "transcricao_inicio.json", dados / "transcricao_janela.json", dados / "transcricao_resto.json"]
DURACOES = (1.5, 3.0, 6.0, 12.0)
LIMIARES = np.round(np.arange(0.0, 0.80, 0.01), 2)


def acerto(rotulos, quem, dur, limiar):
    """% do tempo de fala com o locutor certo, dados as semelhanças (n x 2: Lula, Flávio) de cada janela."""
    melhor = np.where(quem[:, 0] >= quem[:, 1], "Lula", "Flávio")
    pred = np.where(quem.max(axis=1) >= limiar, melhor, MEDIACAO)
    return float((dur * (pred == rotulos)).sum() / dur.sum()), pred


def cmd_medir(dados, saida, reusar=False):
    r = Reconhecedor.carregar(saida, limiar=0.0)
    trans = [p for p in transcricoes_alinhamento(dados, saida) if p.exists()]
    escriba = dados / "referencia" / "escriba_aosfatos.json"
    ivs = intervalos_escriba(escriba, trans)
    desl = deslocamento_escriba(tokens_escriba(escriba), trans)[1]
    t0, t1 = min(i["start"] for i in ivs) - 1, max(i["end"] for i in ivs) + 1
    print(f"referência: {len(ivs)} intervalos, {t0:.0f}-{t1:.0f} s")

    # conferência do alinhamento do Escriba contra as falas da Band (bloco1_final), no confronto livre
    falas = json.loads((dados / "bloco1_final.json").read_text(encoding="utf-8"))["turns"]
    conf = [(i["end"] - i["start"], rotulo_por_cobertura(i["start"], i["end"], falas, 0.5) == i["speaker"])
            for i in ivs if BLOCO1[0] <= i["start"] and i["end"] <= BLOCO1[1]]
    alinhamento = sum(d for d, ok in conf if ok) / sum(d for d, _ in conf)
    audio = dados / "raw" / "debate_audio.m4a"
    cache = saida / "medicao_semelhancas.npz"

    if reusar and cache.exists():  # só refaz a escolha do limiar e o relatório
        z = np.load(cache)
        res = {d: {"jan": [(a, b, q) for (a, b), q in zip(z[f"jan_{d}"], z[f"quem_{d}"])],
                   "sims": z[f"sims_{d}"], "tempos": z[f"tempos_{d}"]} for d in DURACOES}
        relatorio(saida, res, alinhamento, trans, desl, audio)
        return

    sig = le_audio(audio, t0, t1)
    res = {}
    for dur in DURACOES:
        jan = janelas(ivs, dur)
        sims, tempos = [], []
        for a, b, _ in jan:
            seg = sig[int((a - t0) * TAXA): int((b - t0) * TAXA)]
            c0 = time.perf_counter()
            v = r.quem_fala(seg)
            tempos.append(time.perf_counter() - c0)
            sims.append([v.semelhancas["Lula"], v.semelhancas["Flávio"]])
        res[dur] = {"jan": jan, "sims": np.array(sims), "tempos": np.array(tempos)}
        print(f"{dur} s: {len(jan)} janelas, {np.median(tempos) * 1000:.0f} ms/janela")
    np.savez(cache, **{f"sims_{d}": res[d]["sims"] for d in res}, **{f"tempos_{d}": res[d]["tempos"] for d in res},
             **{f"jan_{d}": np.array([(a, b) for a, b, _ in res[d]["jan"]]) for d in res},
             **{f"quem_{d}": np.array([q for _, _, q in res[d]["jan"]]) for d in res})
    relatorio(saida, res, alinhamento, trans, desl, audio)


def _partes(jan):
    ini = np.array([a for a, _, _ in jan])
    fim = np.array([b for _, b, _ in jan])
    quem = np.array([q for _, _, q in jan])
    antes = fim <= BLOCO1[0]
    depois = ini >= BLOCO1[1]
    dentro = ~antes & ~depois
    return ini, fim, quem, {"calibração (antes do confronto livre)": antes,
                            "teste (depois do confronto livre)": depois,
                            "confronto livre (de onde saíram os perfis)": dentro}


def tempo_cpu(threads_lista=(1, 2, 4, 8), duracoes=(1.5, 3.0, 6.0, 12.0, 30.0), repet=8):
    """Mede o tempo de um embedding ECAPA por trecho, por nº de threads do torch (o Whisper usa 8)."""
    import torch
    cod = codificador_ecapa()
    rng = np.random.default_rng(0)
    out = {}
    for th in threads_lista:
        torch.set_num_threads(th)
        for d in duracoes:
            x = (rng.standard_normal(int(d * TAXA)) * 0.1).astype(np.float32)
            cod(x)
            ts = []
            for _ in range(repet):
                c0 = time.perf_counter()
                cod(x)
                ts.append(time.perf_counter() - c0)
            out[(th, d)] = float(np.median(ts))
    torch.set_num_threads(8)
    return out


def tempo_sob_carga(audio, espera=40):
    """Como tempo_cpu, mas com o transcribe.py (faster-whisper, 8 threads) rodando ao lado, como no ao vivo."""
    saida = Path(tempfile.gettempdir()) / "vozes_carga.json"
    p = subprocess.Popen([sys.executable, "-P", str(Path(__file__).with_name("transcribe.py")), str(audio),
                          "16300", "16900", str(saida), "large-v3-turbo"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(espera)  # carga do modelo e corte do áudio; depois disso o Whisper está transcrevendo
        out = tempo_cpu(threads_lista=(1, 2, 8), duracoes=(3.0, 12.0, 30.0), repet=5)
        ainda = p.poll() is None
    finally:
        p.kill()
        p.wait()
        saida.unlink(missing_ok=True)
    return out, ainda


def relatorio(saida, res, alinhamento, trans, desl, audio):
    perfis = json.loads((saida / "perfis.json").read_text(encoding="utf-8"))
    L = []
    w = L.append
    # curva por limiar, em cada parte e duração
    curvas = {}
    for dur, x in res.items():
        ini, fim, quem, partes = _partes(x["jan"])
        d = fim - ini
        for nome, m in partes.items():
            curvas[(dur, nome)] = [acerto(quem[m], x["sims"][m], d[m], t)[0] for t in LIMIARES]
    cal, tst, b1 = ("calibração (antes do confronto livre)", "teste (depois do confronto livre)",
                    "confronto livre (de onde saíram os perfis)")
    ref = 3.0
    c = np.array(curvas[(ref, cal)])
    limiar = float(LIMIARES[int(np.flatnonzero(c >= c.max() - 0.002).mean().round())])  # meio do platô
    # grava o limiar escolhido nos perfis
    perfis["limiar"] = limiar
    perfis["limiar_fonte"] = f"relatorio.md ({datetime.now().isoformat(timespec='seconds')})"
    (saida / "perfis.json").write_text(json.dumps(perfis, ensure_ascii=False, indent=1), encoding="utf-8")

    w("# Perfis de voz: medição no debate da Band\n")
    w(f"Gerado por `python -P scripts/vozes_medir.py medir` em {datetime.now():%d/%m/%Y %H:%M}. "
      "Arquivo local, fora do git: usa a transcrição de referência do Aos Fatos (Escriba) como gabarito.\n")
    w("## Resumo\n")
    i_lim = int(np.flatnonzero(LIMIARES == limiar)[0])
    w(f"- **Limiar escolhido: {limiar:.2f}** (cosseno com o perfil mais parecido; abaixo dele, Mediação).")
    for dur in DURACOES:
        w(f"- Trechos de {dur:g} s, com o limiar: **{curvas[(dur, tst)][i_lim] * 100:.1f}%** do tempo de fala "
          f"com o locutor certo no teste (depois do confronto livre); {curvas[(dur, cal)][i_lim] * 100:.1f}% na "
          f"calibração; {curvas[(dur, b1)][i_lim] * 100:.1f}% no confronto livre (de onde saíram os perfis).")
    w("")
    x = res[ref]
    ini, fim, quem, partes = _partes(x["jan"])
    m = partes[tst]
    mx = x["sims"][m].max(axis=1)
    p10_cand = min(np.percentile(mx[quem[m] == q], 10) for q in ("Lula", "Flávio"))
    p90_med = np.percentile(mx[quem[m] == MEDIACAO], 90)
    w("**Leitura:**\n")
    w(f"- Por que {limiar:.2f}: no teste, 90% dos trechos de candidato ficam acima de {p10_cand:.2f} de semelhança "
      f"e 90% dos de mediação abaixo de {p90_med:.2f}. O limiar fica no meio da faixa em que o acerto da calibração "
      "é máximo, o que deixa folga dos dois lados: para baixo, se o áudio do g1 capturado pelo som do computador "
      "baixar a semelhança dos candidatos; para cima, se a voz do mediador da Globo for mais parecida com a de um "
      "candidato do que a dos jornalistas da Band.")
    w(f"- Trechos de 1,5 s erram mais ({curvas[(1.5, tst)][i_lim] * 100:.1f}% no teste) porque o embedding fica "
      "mais ruidoso e cai mais vezes abaixo do limiar. Para o ticket 09: classificar trechos de pelo menos ~3 s "
      "(ou juntar trechos curtos ao vizinho antes de decidir).")
    w(f"- O confronto livre tem acerto menor ({curvas[(ref, b1)][i_lim] * 100:.1f}% em 3 s) mesmo sendo a origem "
      "dos perfis: é a parte com trocas rápidas, apartes e fala sobreposta, onde o próprio gabarito tem mais "
      "ruído (ver a conferência do alinhamento abaixo). É a parte que mais se parece com um confronto direto ao "
      "vivo, então convém tratá-la como o caso difícil.")
    w("")
    w("## Como foi medido\n")
    w(f"- **Perfis:** {perfis['fonte']}. Janelas: {perfis['janelas']}; segundos: {perfis['segundos']}. "
      f"Modelo `{MODELO}` (o mesmo do `diarize.py`), embeddings normalizados, perfil = média renormalizada.")
    w("- **Referência:** `referencia/escriba_aosfatos.json` (transcrição revisada do Aos Fatos, debate inteiro, "
      "locutores Lula, Flávio Bolsonaro e Jornalista = Mediação). O tempo do Escriba é levado ao da gravação "
      "alinhando as palavras dele às do Whisper (" + ", ".join(f"`{p.name}`" for p in trans) + f"): deslocamento "
      f"mediano {np.median(desl):.2f} s (p1-p99: {np.percentile(desl, 1):.2f} a {np.percentile(desl, 99):.2f} s), "
      f"e não os {ESCRIBA_OFFSET} s fixos do `avaliar.py`. Os intervalos de fala saem do tempo das palavras; "
      "palavras com tempo impossível (o arquivo tem algumas) ficam de fora e sobreposições entre locutores são "
      "cortadas.")
    w(f"- **Conferência do alinhamento:** no confronto livre, {alinhamento * 100:.1f}% do tempo de fala do Escriba "
      "tem o mesmo locutor que a referência da Band (`bloco1_final.json`). O desvio que sobra mede ruído de tempo "
      "e de locutor no próprio gabarito, e é um teto para a medição.")
    w("- **Trechos:** cada intervalo de referência é cortado em trechos de ~1,5, 3, 6 ou 12 s que cobrem o "
      "intervalo inteiro, e cada trecho é classificado sozinho. A métrica é a fração do tempo de fala com o "
      "locutor certo (trechos ponderados pela duração). Os trechos nunca cruzam troca de locutor; no ao vivo isso "
      "acontece, e o efeito é medido no ticket 09.")
    w("- **Partes:** a medição separa o que veio antes do confronto livre (blocos de perguntas de jornalistas: "
      "**calibração**, onde o limiar é escolhido), o que veio depois (**teste**, número a citar) e o próprio "
      "confronto livre (os perfis saíram dele, então é otimista).\n")
    w("## Tempo de fala na referência\n")
    w("| parte | Lula | Flávio | Mediação |")
    w("|---|---:|---:|---:|")
    ini, fim, quem, partes = _partes(res[ref]["jan"])
    for nome, m in partes.items():
        w(f"| {nome} | " + " | ".join(f"{(fim - ini)[m & (quem == q)].sum() / 60:.1f} min"
                                      for q in ("Lula", "Flávio", MEDIACAO)) + " |")
    w("")
    w("## Escolha do limiar\n")
    w(f"O limiar é escolhido só na calibração, com trechos de {ref:g} s (o tamanho das janelas dos perfis): o meio "
      "da faixa de limiares cujo acerto fica a até 0,2 ponto percentual do máximo. Limiar 0 = nunca Mediação.\n")
    w("| limiar | " + " | ".join(f"{d:g} s cal." for d in DURACOES) + " | "
      + " | ".join(f"{d:g} s teste" for d in DURACOES) + " |")
    w("|---:|" + "---:|" * (2 * len(DURACOES)))
    for i, t in enumerate(LIMIARES):
        if i % 5 and i != i_lim:
            continue
        marca = " **←**" if i == i_lim else ""
        w(f"| {t:.2f}{marca} | " + " | ".join(f"{curvas[(d, cal)][i] * 100:.1f}%" for d in DURACOES) + " | "
          + " | ".join(f"{curvas[(d, tst)][i] * 100:.1f}%" for d in DURACOES) + " |")
    w("")
    w(f"## Confusão no teste (trechos de {ref:g} s, limiar {limiar:.2f})\n")
    x = res[ref]
    ini, fim, quem, partes = _partes(x["jan"])
    m = partes[tst]
    _, pred = acerto(quem[m], x["sims"][m], (fim - ini)[m], limiar)
    d = (fim - ini)[m]
    q = quem[m]
    w("| referência ↓ / atribuído → | Lula | Flávio | Mediação | acerto |")
    w("|---|---:|---:|---:|---:|")
    for a in ("Lula", "Flávio", MEDIACAO):
        tot = d[q == a].sum()
        cel = [d[(q == a) & (pred == b)].sum() for b in ("Lula", "Flávio", MEDIACAO)]
        w(f"| {a} | " + " | ".join(f"{v / 60:.1f} min" for v in cel)
          + f" | {d[(q == a) & (pred == a)].sum() / tot * 100:.1f}% |")
    w("")
    w("Semelhança com o perfil mais parecido, por locutor de referência (teste, mediana e percentis 10/90):\n")
    w("| referência | p10 | mediana | p90 |")
    w("|---|---:|---:|---:|")
    mx = x["sims"][m].max(axis=1)
    for a in ("Lula", "Flávio", MEDIACAO):
        v = mx[q == a]
        w(f"| {a} | {np.percentile(v, 10):.2f} | {np.median(v):.2f} | {np.percentile(v, 90):.2f} |")
    w("")
    w("## Tempo de processamento na CPU\n")
    w("Os números absolutos variam com o que mais estiver rodando na máquina (outros processos estavam ativos "
      "durante esta medição). Na medição acima (torch com 8 threads, sem o Whisper ao lado), mediana por "
      "trecho:\n")
    w("| trecho | ms por trecho | fração do tempo real |")
    w("|---:|---:|---:|")
    for dur in DURACOES:
        t = np.median(res[dur]["tempos"])
        w(f"| {dur:g} s | {t * 1000:.0f} ms | {t / dur * 100:.1f}% |")
    w("")
    tc = tempo_cpu()
    w("Por número de threads do torch (sinal sintético, mediana de 8 execuções). No ao vivo o Whisper ocupa "
      "a CPU ao mesmo tempo, então vale olhar as linhas de 1 e 2 threads:\n")
    durs = sorted({d for _, d in tc})
    w("| threads | " + " | ".join(f"{d:g} s" for d in durs) + " |")
    w("|---:|" + "---:|" * len(durs))
    for th in sorted({t for t, _ in tc}):
        w(f"| {th} | " + " | ".join(f"{tc[(th, d)] * 1000:.0f} ms" for d in durs) + " |")
    w("")
    tc, ainda = tempo_sob_carga(audio)
    w("**Sob carga**: o mesmo, com o `transcribe.py` (faster-whisper `large-v3-turbo` int8, 8 threads) transcrevendo "
      "ao lado, como no ao vivo" + ("" if ainda else " (atenção: a transcrição terminou antes do fim da medição)")
      + ":\n")
    durs = sorted({d for _, d in tc})
    w("| threads | " + " | ".join(f"{d:g} s" for d in durs) + " |")
    w("|---:|" + "---:|" * len(durs))
    for th in sorted({t for t, _ in tc}):
        w(f"| {th} | " + " | ".join(f"{tc[(th, d)] * 1000:.0f} ms" for d in durs) + " |")
    w("")
    pior = max(tc[k] / k[1] for k in tc)
    w(f"**Conclusão:** no pior caso medido sob carga, o reconhecimento gasta {pior * 100:.0f}% da duração do trecho, "
      "bem abaixo do tempo real e pequeno perto da transcrição (o Whisper roda a ~4× o tempo real, isto é, ~25%). "
      "Cabe no ao vivo na CPU. O `torch.set_num_threads` vale para o processo inteiro; se o ticket 09 rodar o "
      "ECAPA no mesmo processo do Whisper, não precisa mudar o número de threads.\n")
    w("## Limitações e próximos passos\n")
    w("- Os perfis são da Band (YouTube, AAC). No ao vivo o áudio vem do g1 pelo som do computador: outro canal. "
      "O ensaio (tickets 09/10) mede isso de ponta a ponta; se o acerto cair, recalcular o limiar com o áudio "
      "capturado do ensaio.")
    w("- A mediação da Globo é outra pessoa. O limiar não depende de perfil de mediação, mas a voz de um "
      "mediador parecida com a de um candidato pode passar do limiar.")
    w("- Trechos com troca de locutor no meio ficam com quem fala mais; o fechamento de fala do ticket 09 deve "
      "evitar trechos longos com mais de uma voz.")
    (saida / "relatorio.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"limiar {limiar:.2f}; relatório -> {saida / 'relatorio.md'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("comando", choices=["perfis", "medir"])
    ap.add_argument("--dados", type=Path, help="pasta de dados (padrão: CHECAGEM_DADOS ou data/ do checkout principal)")
    ap.add_argument("--saida", type=Path, help="pasta dos perfis e do relatório (padrão: <dados>/vozes)")
    ap.add_argument("--reusar", action="store_true",
                    help="medir: reaproveita as semelhanças de medicao_semelhancas.npz (só refaz limiar e relatório)")
    a = ap.parse_args()
    dados = pasta_dados(a.dados)
    saida = a.saida or dados / "vozes"
    saida.mkdir(parents=True, exist_ok=True)
    if a.comando == "perfis":
        cmd_perfis(dados, saida)
    else:
        cmd_medir(dados, saida, a.reusar)


if __name__ == "__main__":
    main()
