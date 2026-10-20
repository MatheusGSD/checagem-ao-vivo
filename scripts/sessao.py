"""Sessão de checagem: recebe falas fechadas e mantém as ligações entre afirmações dos candidatos e
checagens que agências já publicaram.

Para cada fala de candidato: o Claude extrai as afirmações checáveis (prompts/extrair_afirmacoes.md),
a base é consultada por sentido e o Claude confirma quais candidatas tratam da mesma afirmação
(prompts/confirmar_ligacao.md). Ligações confirmadas vão para a fila; afirmações sem nenhuma
confirmada ficam "sem par". A cada atualização da base (`atualiza_base`), as afirmações sem par são
conferidas de novo contra os registros novos (ligação tardia). O estado sai no formato do arquivo de
dados da página (docs/globo/formato-dos-dados.md).

Todo o estado muda por eventos (`Sessao.eventos`, também entregues a `ao_registrar`), que levam o que
o Claude respondeu: repetir os eventos reconstrói o estado sem chamar o Claude de novo.

Uso (repassa as falas de referência do debate da Band, com o Claude de verdade):
  python -P scripts/sessao.py band [--falas data/bloco1_final.json] [--dados DADOS] [--saida PASTA]
"""
import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checagens import FOF_AGENCIA  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
PROMPTS = RAIZ / "prompts"
EXTRAIR = "extrair_afirmacoes.md"
CONFIRMAR = "confirmar_ligacao.md"
MODELO = "claude-opus-5-5"
BRASILIA = timezone(timedelta(hours=-3))
CANDIDATOS = ("Lula", "Flávio")
N_CANDIDATAS = 8
CONFIRMACOES_EM_PARALELO = 4
NUMERO_DESDE = "2025-01-01"  # afirmações numéricas só ligam com checagens a partir desta data

TXT = {"type": "string"}
ESQUEMA_EXTRACAO = {
    "type": "object", "additionalProperties": False, "required": ["afirmacoes"],
    "properties": {"afirmacoes": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["trecho", "reformulacao", "tipo"],
        "properties": {"trecho": TXT, "reformulacao": TXT, "tipo": {"type": "string", "enum": ["numero", "perene"]}},
    }}},
}
ESQUEMA_CONFIRMACAO = {
    "type": "object", "additionalProperties": False, "required": ["confirmadas"],
    "properties": {"confirmadas": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["url", "nota", "motivo"],
        "properties": {"url": TXT, "nota": {"type": "number"}, "motivo": TXT},
    }}},
}


def _chamar_padrao(etapa, prompt, conteudo, schema):
    import agente

    return agente.chama(etapa, prompt, conteudo, schema)


class Sessao:
    def __init__(self, indice, chamar=None, relogio=None, ao_registrar=None, meta=None):
        """indice: base_checagens.Indice. chamar(etapa, prompt, conteudo, schema) -> (saída, registro),
        por padrão agente.chama. relogio() -> datetime com fuso. ao_registrar(evento) recebe cada
        evento assim que acontece (para o registro só de acréscimo). meta: campos de `meta` do
        arquivo de dados que mudam o padrão (título, debate, início do debate, agências...)."""
        self.indice = indice
        self._meta = meta or {}
        self._chamar = chamar or _chamar_padrao
        self._relogio = relogio or (lambda: datetime.now(BRASILIA))
        self._ao_registrar = ao_registrar
        self.eventos = []
        self._falas = []
        self._propostas = []
        self._sem_par = []
        self._descartados = []
        self._erros = []

    # ---------- interface ----------

    def recebe_fala(self, quem, inicio_s, texto):
        """Uma fala fechada: quem ("Lula", "Flávio" ou outro), início em segundos desde o 00:00 e texto."""
        fala = self._nova_fala(quem, inicio_s, texto)
        self._registra({"tipo": "fala", "fala": fala})  # registrada antes das chamadas ao Claude
        for evento in self._analisa(fala):
            self._registra(evento)

    def recebe_falas(self, falas, paralelo=3):
        """Várias falas [(quem, início, texto)], com o mesmo resultado de recebê-las uma a uma nessa
        ordem (repasse de uma gravação). Analisa até `paralelo` falas ao mesmo tempo."""
        n0 = len(self._falas)
        falas = [{**self._nova_fala(q, t, x), "id": f"f{n0 + k:04d}"} for k, (q, t, x) in enumerate(falas, 1)]
        with ThreadPoolExecutor(max_workers=paralelo) as ex:
            for fala, eventos in zip(falas, ex.map(self._analisa, falas)):
                for evento in [{"tipo": "fala", "fala": fala}] + eventos:
                    self._registra(evento)

    def atualiza_base(self, registros):
        """Novos registros de checagem (atualização da base durante o debate, ver
        base_checagens.atualiza): entram na base e as afirmações sem par são conferidas de novo só
        contra eles (ligação tardia). As que ganham par seguem o caminho de qualquer ligação."""
        ja = {r["id"] for r in self.indice.registros}
        novos = list({r["id"]: r for r in registros if r["id"] not in ja}.values())
        if not novos:
            return
        self._registra({"tipo": "base", "registros": novos})  # registrada antes das chamadas ao Claude
        ids = {r["id"] for r in novos}
        with ThreadPoolExecutor(max_workers=CONFIRMACOES_EM_PARALELO) as ex:
            eventos = list(ex.map(lambda a: self._confirma(a, entre=ids), list(self._sem_par)))
        for evento in eventos:
            if evento["tipo"] != "confirmacao" or evento["candidatas"]:  # sem candidata nova: nada mudou
                self._registra({**evento, "tardia": True})

    def _nova_fala(self, quem, inicio_s, texto):
        return {"id": f"f{len(self._falas) + 1:04d}", "quem": quem, "inicio_s": inicio_s, "texto": texto}

    def fila(self):
        """Ligações propostas à espera do operador, na ordem em que foram propostas."""
        return [dict(p) for p in self._propostas]

    def descartados(self):
        """Afirmações extraídas e descartadas (trecho que não aparece letra a letra na fala), com o motivo."""
        return [dict(d) for d in self._descartados]

    def erros(self):
        """Chamadas ao Claude que falharam (recusa, corte, rede), com a fala e a mensagem."""
        return [dict(e) for e in self._erros]

    def exporta(self):
        """O estado no formato do arquivo de dados da página (docs/globo/formato-dos-dados.md)."""
        from contrato_painel import valida

        dados = {
            "versao_contrato": 1,
            "meta": {**self._meta_padrao(), **self._meta, "atualizado_em": self._agora()},
            "ligacoes": [],
            "sem_par": [{k: a[k] for k in ("minutagem_s", "quem", "trecho", "reformulacao")} for a in self._sem_par],
            "correcoes": [],
        }
        if problemas := valida(dados):
            raise ValueError("arquivo de dados fora do contrato:\n" + "\n".join(problemas))
        return dados

    # ---------- análise (chama o Claude; não muda o estado) ----------

    def _analisa(self, fala):
        """Eventos que a fala gera: afirmações extraídas, confirmação de cada uma e erros."""
        if fala["quem"] not in CANDIDATOS:  # mediação e outros: só entram no registro
            return []
        try:
            saida, reg = self._chamar(f"extrair_{fala['id']}", EXTRAIR,
                                      f"Quem fala: {fala['quem']}\n\nFala:\n{fala['texto']}", ESQUEMA_EXTRACAO)
        except (Exception, SystemExit) as e:  # agente.chama encerra com SystemExit em recusa ou corte
            return [_erro("extrair", fala["id"], fala["quem"], fala["inicio_s"], e)]
        extraidas = [{"id": f"{fala['id']}a{k}", "fala_id": fala["id"], "quem": fala["quem"],
                      "minutagem_s": fala["inicio_s"], **a}
                     for k, a in enumerate(saida["afirmacoes"], 1)]
        # como no agente da Band, o trecho tem de estar na fala letra a letra; se não estiver, sai
        afirmacoes = [a for a in extraidas if a["trecho"].strip() and a["trecho"] in fala["texto"]]
        descartadas = [{**a, "motivo": "o trecho não aparece letra a letra na fala"}
                       for a in extraidas if a not in afirmacoes]
        eventos = [{"tipo": "afirmacoes", "fala_id": fala["id"], "afirmacoes": afirmacoes,
                    "descartadas": descartadas, "chamada": reg}]
        # uma chamada por afirmação, em paralelo para não atrasar a fila; os eventos saem na ordem
        with ThreadPoolExecutor(max_workers=CONFIRMACOES_EM_PARALELO) as ex:
            eventos += list(ex.map(self._confirma, afirmacoes))
        return eventos

    def _confirma(self, a, entre=None):
        # números mudam com o tempo: só valem checagens recentes; fatos perenes valem de qualquer data
        desde = NUMERO_DESDE if a["tipo"] == "numero" else None
        try:
            candidatas = [r for _, r in self.indice.busca(a["reformulacao"], N_CANDIDATAS, desde=desde, entre=entre)]
            if not candidatas:
                return {"tipo": "confirmacao", "afirmacao": a, "candidatas": [], "confirmadas": [],
                        "ignoradas": [], "chamada": None}
            linhas = [f"- URL: {r['url']}\n  agência: {r['agencia']} · data: {r['data'] or 'sem data'}\n"
                      f"  frase checada: {r['frase']}" + (f"\n  quem disse: {r['quem_disse']}" if r.get("quem_disse") else "")
                      for r in candidatas]
            conteudo = (f"Afirmação\n  quem disse: {a['quem']}\n  trecho: {a['trecho']}\n"
                        f"  reformulação: {a['reformulacao']}\n  tipo: {a['tipo']}\n\nCandidatas:\n" + "\n".join(linhas))
            saida, reg = self._chamar(f"confirmar_{a['id']}", CONFIRMAR, conteudo, ESQUEMA_CONFIRMACAO)
        except (Exception, SystemExit) as e:
            return {**_erro("confirmar", a["fala_id"], a["quem"], a["minutagem_s"], e), "afirmacao": a}
        por_url = {r["url"]: r for r in candidatas}
        confirmadas, ignoradas, vistas = [], [], set()
        for c in saida["confirmadas"]:
            if c["url"] in por_url and 0 <= c["nota"] <= 1 and c["url"] not in vistas:
                vistas.add(c["url"])
                confirmadas.append({"checagem": _resumo(por_url[c["url"]]), "nota": c["nota"], "motivo": c["motivo"]})
            else:  # URL que não estava entre as candidatas, nota fora de 0 a 1 ou repetida
                ignoradas.append(c)
        return {"tipo": "confirmacao", "afirmacao": a, "candidatas": [r["id"] for r in candidatas],
                "confirmadas": confirmadas, "ignoradas": ignoradas, "chamada": reg}

    # ---------- estado (só muda por eventos) ----------

    def _registra(self, evento):
        evento = {"n": len(self.eventos) + 1, "em": self._agora(), **evento}
        self.eventos.append(evento)
        if self._ao_registrar:
            self._ao_registrar(evento)
        self._aplica(evento)

    def _aplica(self, e):
        if e["tipo"] == "fala":
            self._falas.append(e["fala"])
        elif e["tipo"] == "erro":
            self._erros.append({k: v for k, v in e.items() if k not in ("tipo", "n")})
        elif e["tipo"] == "afirmacoes":
            self._descartados += e["descartadas"]
        elif e["tipo"] == "base":  # repetir o registro também refaz a base em memória
            self.indice.acrescenta(e["registros"])
        elif e["tipo"] == "confirmacao" and not e["confirmadas"]:
            if not e.get("tardia"):  # reconferida sem par: continua onde estava
                self._sem_par.append(e["afirmacao"])
        elif e["tipo"] == "confirmacao":
            a = e["afirmacao"]
            self._sem_par = [x for x in self._sem_par if x["id"] != a["id"]]  # ligação tardia sai de "sem par"
            self._propostas.append({
                "id": f"lig-{len(self._propostas) + 1:04d}", "afirmacao_id": a["id"],
                "minutagem_s": a["minutagem_s"], "quem": a["quem"], "fala": self._fala(a["fala_id"])["texto"],
                "trecho": a["trecho"], "reformulacao": a["reformulacao"], "tipo": a["tipo"],
                "nota": max(c["nota"] for c in e["confirmadas"]), "proposta_em": e["em"],
                "ligada_depois": bool(e.get("tardia")),
                "checagens": [{**c["checagem"], "nota": c["nota"], "motivo": c["motivo"]} for c in e["confirmadas"]],
            })

    def _fala(self, fala_id):
        return next(f for f in self._falas if f["id"] == fala_id)

    def _agora(self):
        return self._relogio().isoformat(timespec="seconds")

    def _meta_padrao(self):
        agencias = sorted({r["agencia"] for r in self.indice.registros if r.get("agencia")})
        fof = any((r.get("fonte_coleta") or "").startswith("fato_ou_fake") for r in self.indice.registros)
        return {
            "titulo": "Lula × Flávio: o que as agências já checaram",
            "debate": "Debate do 2º turno · TV Globo · 23/10/2026",
            "inicio_debate": None,
            "exemplo": False,
            "encerrado": False,
            "fontes": [{"nome": "Google Fact Check Tools API (ClaimReview)",
                        "url": "https://developers.google.com/fact-check/tools/api"}]
                      + ([{"nome": "RSS e sitemap do Fato ou Fake (g1)", "url": "https://g1.globo.com/fato-ou-fake/"}]
                         if fof else []),
            "agencias_incluidas": [{"nome": a, "via": "RSS e sitemap do g1" if a == FOF_AGENCIA
                                    else "ClaimReview na API do Google"} for a in agencias],
            "agencias_excluidas": [{"nome": "Lupa", "motivo": "Não publica ClaimReview e não autorizou a coleta "
                                                              "das checagens até agora."}],
            "modelo": MODELO,
            "prompts": [{"arquivo": f"prompts/{p}", "sha256": sha256_prompt(p)} for p in (EXTRAIR, CONFIRMAR)],
            "limiar_automatico": None,  # publicação automática desligada nesta etapa
        }


def _erro(etapa, fala_id, quem, minutagem_s, excecao):
    return {"tipo": "erro", "etapa": etapa, "fala_id": fala_id, "quem": quem, "minutagem_s": minutagem_s,
            "mensagem": f"{type(excecao).__name__}: {excecao}"}


def _resumo(r):
    """O que a ligação guarda da checagem (só metadados)."""
    return {k: r.get(k) for k in ("id", "agencia", "data", "rotulo", "url", "frase")}


def sha256_prompt(nome):
    return hashlib.sha256((PROMPTS / nome).read_text(encoding="utf-8").encode()).hexdigest()


# ---------- repasse das falas de referência da Band ----------

def band(falas_json, pasta_base_checagens, saida, paralelo=3):
    """Passa as falas de bloco1_final.json pela sessão, com o Claude de verdade, e grava em `saida`:
    painel.json (arquivo de dados da página), fila.json (ligações propostas, descartes e erros),
    eventos.jsonl (registro da sessão) e agente/ (cada chamada ao Claude)."""
    import json
    import threading

    import agente
    from base_checagens import Indice

    saida = Path(saida)
    if (saida / "eventos.jsonl").exists():
        sys.exit(f"{saida} já tem um repasse; escolha outra pasta com --saida")
    saida.mkdir(parents=True, exist_ok=True)
    turnos = json.loads(Path(falas_json).read_text(encoding="utf-8"))["turns"]
    t0 = turnos[0]["start"]
    indice = Indice.da_pasta(Path(pasta_base_checagens))
    print(f"base: {len(indice.registros)} checagens; falas: {len(turnos)}")

    trava, uso = threading.Lock(), {"chamadas": 0, "entrada": 0, "saida": 0}

    def chamar(etapa, prompt, conteudo, schema):
        out, reg = agente.chama(etapa, prompt, conteudo, schema, logs=saida / "agente")
        with trava:
            uso["chamadas"] += 1
            uso["entrada"] += reg["tokens"]["entrada"]
            uso["saida"] += reg["tokens"]["saida"]
        return out, reg

    eventos = (saida / "eventos.jsonl").open("a", encoding="utf-8")

    def registra(evento):
        eventos.write(json.dumps(evento, ensure_ascii=False) + "\n")
        eventos.flush()

    s = Sessao(indice, chamar=chamar, ao_registrar=registra, meta={
        "debate": "Debate do 2º turno · Band · 18/10/2026 · confronto livre (teste da sessão de checagem)",
        "encerrado": True,
    })
    s.recebe_falas([(t["speaker"], round(t["start"] - t0, 1), t["text"]) for t in turnos], paralelo=paralelo)
    eventos.close()

    dados = s.exporta()
    (saida / "painel.json").write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    resumo = {
        "falas": len(turnos), "falas_de_candidato": sum(t["speaker"] in CANDIDATOS for t in turnos),
        "afirmacoes": sum(len(e["afirmacoes"]) for e in s.eventos if e["tipo"] == "afirmacoes"),
        "ligacoes_propostas": len(s.fila()), "sem_par": len(dados["sem_par"]),
        "descartados": len(s.descartados()), "erros": len(s.erros()),
        "chamadas_claude": uso["chamadas"], "tokens": {"entrada": uso["entrada"], "saida": uso["saida"]},
        "base": str(pasta_base_checagens), "checagens_na_base": len(indice.registros),
    }
    (saida / "fila.json").write_text(json.dumps({
        "resumo": resumo, "fila": s.fila(), "descartados": s.descartados(), "erros": s.erros(),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False, indent=1))
    print(f"-> {saida / 'painel.json'}, {saida / 'fila.json'}, {saida / 'eventos.jsonl'}")


def main():
    import argparse

    from base_checagens import pasta_base, raiz_principal

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("comando", choices=["band"])
    ap.add_argument("--falas", help="falas de referência (padrão: data/bloco1_final.json do checkout principal)")
    ap.add_argument("--dados", help="pasta de dados da base (padrão: CHECAGEM_DADOS ou data/ do checkout principal)")
    ap.add_argument("--saida", help="pasta de saída (padrão: data/sessao_band/ do checkout principal)")
    ap.add_argument("--paralelo", type=int, default=3, help="falas analisadas ao mesmo tempo")
    a = ap.parse_args()
    dados = raiz_principal() / "data"
    band(a.falas or dados / "bloco1_final.json", pasta_base(a.dados), a.saida or dados / "sessao_band", a.paralelo)


if __name__ == "__main__":
    main()
