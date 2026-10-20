"""Sessão de checagem: recebe falas fechadas e mantém as ligações entre afirmações dos candidatos e
checagens que agências já publicaram.

Para cada fala de candidato: o Claude extrai as afirmações checáveis (prompts/extrair_afirmacoes.md),
a base é consultada por sentido e o Claude confirma quais candidatas tratam da mesma afirmação
(prompts/confirmar_ligacao.md). Ligações confirmadas vão para a fila; afirmações sem nenhuma
confirmada ficam "sem par". A cada atualização da base (`atualiza_base`), as afirmações sem par são
conferidas de novo contra os registros novos (ligação tardia). O estado sai no formato do arquivo de
dados da página (docs/globo/formato-dos-dados.md).

Todo o estado muda por eventos (`Sessao.eventos`, também entregues a `ao_registrar`), que levam o que
o Claude respondeu: repetir os eventos reconstrói o estado sem chamar o Claude de novo
(`Sessao.reconstroi`; `Sessao.abre` faz isso a partir de <pasta>/eventos.jsonl e continua gravando lá).

O operador aprova (informando o selo visível na página da agência), recusa, despublica com motivo e
corrige o rótulo. Com limiar (`meta["limiar_automatico"]`, lido de <dados>/avaliacao/limiar.json por
`le_limiar`) e mais de 10 min passados desde o 00:00, a ligação com nota ≥ limiar é publicada sozinha.

Uso (repassa as falas de referência do debate da Band, com o Claude de verdade):
  python -P scripts/sessao.py band [--falas data/bloco1_final.json] [--dados DADOS] [--saida PASTA]
"""
import copy
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
AQUECIMENTO_S = 600  # nos primeiros 10 min desde o 00:00 nada é publicado sozinho
EVENTOS_DE_PUBLICACAO = ("inicio", "aprovacao", "publicacao_automatica", "recusa", "despublicacao", "correcao_rotulo")

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


class AcaoInvalida(ValueError):
    """Ação do operador que não vale no estado atual (ligação inexistente, já decidida, sem motivo...).
    Nada é registrado."""


class Sessao:
    def __init__(self, indice, chamar=None, relogio=None, ao_registrar=None, meta=None,
                 ao_mudar_publicado=None):
        """indice: base_checagens.Indice. chamar(etapa, prompt, conteudo, schema) -> (saída, registro),
        por padrão agente.chama. relogio() -> datetime com fuso. ao_registrar(evento) recebe cada
        evento assim que acontece, antes de ele valer (para o registro só de acréscimo). meta: campos
        de `meta` do arquivo de dados que mudam o padrão (título, debate, início do debate,
        limiar_automatico...). ao_mudar_publicado(dados) recebe o arquivo de dados exportado sempre
        que o conteúdo público muda (tudo menos `meta.atualizado_em`): é o gancho do publicador.
        Ele roda com a sessão travada; deve ser rápido e não chamar ações da sessão."""
        self.indice = indice
        self._meta = meta or {}
        self._chamar = chamar or _chamar_padrao
        self._relogio = relogio or (lambda: datetime.now(BRASILIA))
        self._ao_registrar = ao_registrar
        self._ao_mudar_publicado = ao_mudar_publicado
        self._trava = threading.RLock()  # falas, tela de aprovação e publicador rodam em threads
        self._publico = None  # último conteúdo público avisado a ao_mudar_publicado
        self.eventos = []
        self._falas = []
        self._propostas = []
        self._sem_par = []
        self._descartados = []
        self._erros = []
        self._situacao = {}  # id da ligação -> "publicada", "recusada" ou "despublicada" (sem = pendente)
        self._aguardando = {}  # id -> URLs das checagens que esperam a decisão do operador (a fila)
        self._recusas = {}  # id -> {"urls": checagens recusadas, "motivo": motivo ou None}
        self._publicadas = {}  # id -> ligação no formato do arquivo de dados, na ordem de publicação
        self._correcoes = []
        self._inicio = None  # 00:00 marcado pelo operador (marca_inicio)
        self._marca_publico()

    def _marca_publico(self):
        """O conteúdo público atual passa a ser a referência de ao_mudar_publicado (o publicador grava
        o estado inicial por conta própria, ao começar)."""
        if self._ao_mudar_publicado:
            self._publico = self._conteudo_publico(self.exporta())

    @classmethod
    def reconstroi(cls, indice, eventos, **kw):
        """A sessão no estado em que `eventos` a deixaram, sem chamar o Claude e sem repassar os
        eventos a `ao_registrar` nem decidir publicações de novo (o que foi publicado sozinho está
        nos eventos). kw: os mesmos argumentos do construtor, para os eventos seguintes."""
        s = cls(indice, **kw)
        for e in eventos:
            s.eventos.append(e)
            s._aplica(e)
        s._marca_publico()
        return s

    @classmethod
    def abre(cls, pasta, indice, **kw):
        """Sessão gravada em <pasta>/eventos.jsonl (uma linha JSON por evento, só de acréscimo): se o
        arquivo existe, reconstrói o estado a partir dele; cada evento novo é gravado (com flush e
        fsync) antes de valer. Uma última linha cortada por um travamento é descartada."""
        arquivo = Path(pasta) / "eventos.jsonl"
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        eventos = le_eventos(arquivo)
        registro = arquivo.open("a", encoding="utf-8")

        def grava(evento):
            registro.write(json.dumps(evento, ensure_ascii=False) + "\n")
            registro.flush()
            os.fsync(registro.fileno())

        return cls.reconstroi(indice, eventos, ao_registrar=grava, **kw)

    # ---------- interface ----------

    def recebe_fala(self, quem, inicio_s, texto):
        """Uma fala fechada: quem ("Lula", "Flávio" ou outro), início em segundos desde o 00:00 e texto."""
        with self._trava:
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
        """Ligações propostas à espera do operador, na ordem em que foram propostas, só com as
        checagens que esperam decisão (`nota` é a maior delas). Numa ligação publicada sozinha só com
        parte das checagens, o resto espera aqui com o mesmo id; `ja_publicadas` lista as URLs que já
        estão no painel (vazia nas demais)."""
        with self._trava:
            fila = []
            for p in self._propostas:
                if (urls := self._aguardando.get(p["id"])) is None:
                    continue
                checagens = [c for c in p["checagens"] if c["url"] in urls]
                publicada = self._publicadas.get(p["id"], {"checagens": []})
                fila.append(copy.deepcopy({**p, "checagens": checagens, "nota": max(c["nota"] for c in checagens),
                                           "ja_publicadas": [c["url"] for c in publicada["checagens"]]}))
            return fila

    def recusadas(self):
        """Ligações propostas que o operador recusou, só com as checagens recusadas e o motivo (se ele
        deu um). Recusar o resto de uma ligação publicada sozinha não tira a ligação do painel."""
        with self._trava:
            return [copy.deepcopy({**p, "checagens": [c for c in p["checagens"] if c["url"] in r["urls"]],
                                   "motivo_recusa": r["motivo"]})
                    for p in self._propostas if (r := self._recusas.get(p["id"]))]

    def descartados(self):
        """Afirmações extraídas e descartadas (trecho que não aparece letra a letra na fala), com o motivo."""
        return [dict(d) for d in self._descartados]

    def erros(self):
        """Falhas registradas: chamadas ao Claude (recusa, corte, rede), com a fala e a mensagem, e erros
        de fora da sessão (publicação, transcrição...) entregues por `registra_erro`."""
        with self._trava:
            return [dict(e) for e in self._erros]

    def marca_inicio(self, instante=None):
        """O 00:00 do debate (abertura do mediador), por padrão agora. Vai para `meta.inicio_debate` e
        conta os 10 min de aquecimento da publicação automática. Marcar de novo corrige a marcação."""
        instante = instante or self._relogio()
        if isinstance(instante, str):
            instante = datetime.fromisoformat(instante)
        if instante.tzinfo is None:
            raise AcaoInvalida("o 00:00 precisa de fuso horário")
        self._registra({"tipo": "inicio", "instante": instante.isoformat(timespec="seconds")})

    def aprova(self, ligacao_id, selos=None, manter=None):
        """Publica uma ligação da fila. selos = {URL da checagem: selo visível na página da agência};
        o selo tem prioridade sobre o rótulo dos dados da agência, e checagem sem selo informado sai
        com o rótulo dos dados. manter = URLs das checagens que entram (padrão: todas as da fila).
        Se a ligação já saiu sozinha com parte das checagens, as aprovadas entram nela."""
        with self._trava:
            p = self._pendente(ligacao_id)
            urls = [c["url"] for c in p["checagens"]]
            manter = list(urls if manter is None else manter)
            selos = {u: s.strip() for u, s in (selos or {}).items() if s and s.strip()}
            if fora := [u for u in [*manter, *selos] if u not in urls]:
                raise AcaoInvalida(f"{ligacao_id}: checagem que não está na ligação: {fora[0]}")
            if not manter:
                raise AcaoInvalida(f"{ligacao_id}: escolha ao menos uma checagem (ou recuse a ligação)")
            sem_rotulo = [c["url"] for c in p["checagens"]
                          if c["url"] in manter and c["url"] not in selos and not (c.get("rotulo") or "").strip()]
            if sem_rotulo:
                raise AcaoInvalida(f"{ligacao_id}: a checagem {sem_rotulo[0]} não tem rótulo nos dados; informe o selo")
            self._registra({"tipo": "aprovacao", "ligacao_id": ligacao_id,
                            "checagens": [u for u in urls if u in manter],
                            "selos": {u: s for u, s in selos.items() if u in manter}})

    def recusa(self, ligacao_id, motivo=None):
        """Tira uma ligação da fila sem publicar."""
        with self._trava:
            self._pendente(ligacao_id)
            self._registra({"tipo": "recusa", "ligacao_id": ligacao_id, "motivo": (motivo or "").strip() or None})

    def despublica(self, ligacao_id, motivo):
        """Tira uma ligação publicada do painel. Nada é apagado: ela entra nas correções com o horário,
        o motivo e a ligação como estava publicada."""
        with self._trava:
            self._publicada(ligacao_id)
            if not (motivo or "").strip():
                raise AcaoInvalida("despublicar exige um motivo, que aparece nas correções")
            self._registra({"tipo": "despublicacao", "ligacao_id": ligacao_id, "motivo": motivo.strip()})

    def corrige_rotulo(self, ligacao_id, url, rotulo):
        """Troca o rótulo exibido de uma checagem de uma ligação publicada pelo selo visível na página
        da agência."""
        with self._trava:
            lig = self._publicada(ligacao_id)
            if url not in [c["url"] for c in lig["checagens"]]:
                raise AcaoInvalida(f"{ligacao_id}: checagem que não está na ligação: {url}")
            if not (rotulo or "").strip():
                raise AcaoInvalida("informe o selo visível na página da agência")
            self._registra({"tipo": "correcao_rotulo", "ligacao_id": ligacao_id, "url": url, "rotulo": rotulo.strip()})

    def registra_erro(self, etapa, mensagem):
        """Erro de fora da sessão (publicação, transcrição, API do Google...) para a tela do operador.
        Fica no registro de eventos como os erros do Claude."""
        self._registra({"tipo": "erro", "etapa": etapa, "fala_id": None, "quem": None, "minutagem_s": None,
                        "mensagem": str(mensagem)})

    def retoma_analises(self):
        """Termina as análises que um travamento interrompeu: falas de candidato registradas sem
        extração e afirmações extraídas sem confirmação. Só elas voltam ao Claude."""
        with self._trava:
            feitas = {e.get("fala_id") for e in self.eventos if e["tipo"] in ("afirmacoes", "erro")}
            confirmadas = {e["afirmacao"]["id"] for e in self.eventos
                           if e["tipo"] in ("confirmacao", "erro") and e.get("afirmacao")}
            falas = [f for f in self._falas if f["quem"] in CANDIDATOS and f["id"] not in feitas]
            afirmacoes = [a for e in self.eventos if e["tipo"] == "afirmacoes"
                          for a in e["afirmacoes"] if a["id"] not in confirmadas]
        for a in afirmacoes:
            self._registra(self._confirma(a))
        for fala in falas:
            for evento in self._analisa(fala):
                self._registra(evento)

    def exporta(self):
        """O estado no formato do arquivo de dados da página (docs/globo/formato-dos-dados.md)."""
        from contrato_painel import valida

        with self._trava:
            dados = copy.deepcopy({
                "versao_contrato": 1,
                "meta": {**self._meta_padrao(), **self._meta, **self._meta_eventos(), "atualizado_em": self._agora()},
                "ligacoes": list(self._publicadas.values()),
                "sem_par": [{k: a[k] for k in ("minutagem_s", "quem", "trecho", "reformulacao")} for a in self._sem_par],
                "correcoes": self._correcoes,
            })
        if problemas := valida(dados):
            raise ValueError("arquivo de dados fora do contrato:\n" + "\n".join(problemas))
        return dados

    def _pendente(self, ligacao_id):
        """A ligação como está na fila (só com as checagens que esperam decisão)."""
        if not any(p["id"] == ligacao_id for p in self._propostas):
            raise AcaoInvalida(f"ligação inexistente: {ligacao_id}")
        if ligacao_id not in self._aguardando:
            raise AcaoInvalida(f"{ligacao_id} não está na fila: já foi {self._situacao[ligacao_id]}")
        return next(p for p in self.fila() if p["id"] == ligacao_id)

    def _publicada(self, ligacao_id):
        if ligacao_id not in self._publicadas:
            raise AcaoInvalida(f"{ligacao_id} não está publicada")
        return self._publicadas[ligacao_id]

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
        with self._trava:
            evento = {"n": len(self.eventos) + 1, "em": self._agora(), **evento}
            self.eventos.append(evento)
            if self._ao_registrar:
                self._ao_registrar(evento)
            propostas = len(self._propostas)
            self._aplica(evento)
            for p in self._propostas[propostas:]:  # ligação nova: a política decide se sai sozinha
                self._politica(p)
            self._avisa_publicado()

    def _politica(self, p):
        """Publicação automática: com limiar, depois dos 10 min de aquecimento, vão as checagens com
        nota ≥ limiar (e rótulo nos dados), com o rótulo dos dados da agência. O resto fica na fila."""
        limiar, inicio = self._meta.get("limiar_automatico"), self._inicio_debate()
        if limiar is None or inicio is None:
            return
        if (self._relogio() - datetime.fromisoformat(inicio)).total_seconds() <= AQUECIMENTO_S:
            return
        urls = [c["url"] for c in p["checagens"] if c["nota"] >= limiar and (c.get("rotulo") or "").strip()]
        if urls:
            self._registra({"tipo": "publicacao_automatica", "ligacao_id": p["id"], "checagens": urls,
                            "limiar": limiar})

    def _avisa_publicado(self):
        if not self._ao_mudar_publicado:
            return
        dados = self.exporta()
        if (publico := self._conteudo_publico(dados)) != self._publico:
            self._publico = publico
            self._ao_mudar_publicado(dados)

    @staticmethod
    def _conteudo_publico(dados):
        return json.dumps({**dados, "meta": {k: v for k, v in dados["meta"].items() if k != "atualizado_em"}},
                          sort_keys=True, ensure_ascii=False)

    def _aplica(self, e):
        if e["tipo"] in EVENTOS_DE_PUBLICACAO:
            return self._aplica_publicacao(e)
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
            self._aguardando[self._propostas[-1]["id"]] = [c["checagem"]["url"] for c in e["confirmadas"]]

    def _aplica_publicacao(self, e):
        """Eventos do 00:00, das ações do operador e da publicação automática."""
        tipo, ident = e["tipo"], e.get("ligacao_id")
        if tipo == "inicio":
            self._inicio = e["instante"]
        elif tipo in ("aprovacao", "publicacao_automatica"):
            p = next(p for p in self._propostas if p["id"] == ident)
            selos = e.get("selos", {})
            self._situacao[ident] = "publicada"
            lig = self._publicadas.setdefault(ident, {  # aprovar o resto de uma automática: entra nela
                "id": ident, "minutagem_s": p["minutagem_s"], "quem": p["quem"], "trecho": p["trecho"],
                "reformulacao": p["reformulacao"], "tipo": p["tipo"],
                "origem": "aprovada" if tipo == "aprovacao" else "automatica", "publicada_em": e["em"],
                "ligada_depois": bool(p.get("ligada_depois", False)), "checagens": [],
            })
            novas = {c["url"]: {"agencia": c["agencia"], "data": c["data"],
                                "rotulo": selos.get(c["url"]) or c["rotulo"],
                                "fonte_rotulo": "selo_da_pagina" if c["url"] in selos else "dados_da_agencia",
                                "url": c["url"]}
                     for c in p["checagens"] if c["url"] in e["checagens"]}
            ja = {c["url"]: c for c in lig["checagens"]}
            lig["checagens"] = [ja.get(c["url"]) or novas[c["url"]] for c in p["checagens"]
                                if c["url"] in ja or c["url"] in novas]  # na ordem da proposta
            # a automática deixa o resto na fila; a aprovação decide tudo o que esperava
            resto = [u for u in self._aguardando.pop(ident, []) if u not in novas]
            if tipo == "publicacao_automatica" and resto:
                self._aguardando[ident] = resto
        elif tipo == "recusa":
            self._situacao.setdefault(ident, "recusada")  # o resto de uma automática: ela segue publicada
            self._recusas[ident] = {"urls": self._aguardando.pop(ident), "motivo": e["motivo"]}
        elif tipo == "despublicacao":
            self._situacao[ident] = "despublicada"
            self._aguardando.pop(ident, None)  # o resto que esperava na fila sai junto
            retirada = self._publicadas.pop(ident)
            self._correcoes.append({"em": e["em"], "ligacao_id": ident, "motivo": e["motivo"], "retirado": retirada})
        elif tipo == "correcao_rotulo":
            c = next(c for c in self._publicadas[ident]["checagens"] if c["url"] == e["url"])
            c.update(rotulo=e["rotulo"], fonte_rotulo="selo_da_pagina")

    def _inicio_debate(self):
        return self._inicio or self._meta.get("inicio_debate")

    def _meta_eventos(self):
        return {"inicio_debate": self._inicio} if self._inicio else {}

    def _fala(self, fala_id):
        return next(f for f in self._falas if f["id"] == fala_id)

    def _agora(self):
        return self._relogio().isoformat(timespec="seconds")

    def _agencias(self):
        """meta.agencias_incluidas e agencias_excluidas pela política de data/agencias.json; refeitas
        só quando a base muda de tamanho."""
        from agencias import agencias_do_painel

        n = len(self.indice.registros)
        if getattr(self, "_agencias_cache", (None,))[0] != n:
            self._agencias_cache = (n, agencias_do_painel(self.indice.registros))
        return self._agencias_cache[1]

    def _meta_padrao(self):
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
            **self._agencias(),
            "modelo": MODELO,
            "prompts": [{"arquivo": f"prompts/{p}", "sha256": sha256_prompt(p)} for p in (EXTRAIR, CONFIRMAR)],
            "limiar_automatico": None,  # desligada, a menos que meta traga o limiar (le_limiar)
        }


def _erro(etapa, fala_id, quem, minutagem_s, excecao):
    return {"tipo": "erro", "etapa": etapa, "fala_id": fala_id, "quem": quem, "minutagem_s": minutagem_s,
            "mensagem": f"{type(excecao).__name__}: {excecao}"}


def _resumo(r):
    """O que a ligação guarda da checagem (só metadados)."""
    return {k: r.get(k) for k in ("id", "agencia", "data", "rotulo", "url", "frase")}


def sha256_prompt(nome):
    return hashlib.sha256((PROMPTS / nome).read_text(encoding="utf-8").encode()).hexdigest()


# ---------- persistência e limiar ----------

def le_eventos(arquivo):
    """Os eventos de um registro .jsonl (vazio se o arquivo não existe). Uma última linha incompleta,
    de um travamento no meio da gravação, é cortada do arquivo para o registro seguir só de acréscimo."""
    arquivo = Path(arquivo)
    if not arquivo.exists():
        return []
    bruto = arquivo.read_bytes()
    linhas = bruto.split(b"\n")
    eventos = [json.loads(linha) for linha in linhas[:-1] if linha.strip()]
    if linhas[-1].strip():  # sem o \n final: gravação interrompida
        with arquivo.open("r+b") as f:
            f.truncate(len(bruto) - len(linhas[-1]))
    return eventos


def le_limiar(dados=None):
    """Limiar da publicação automática em <dados>/avaliacao/limiar.json ({"limiar": número ou null,
    "origem", "em"}), escrito pela avaliação da Band. Sem arquivo ou com null: None (desligada).
    dados: --dados, a variável CHECAGEM_DADOS ou a pasta data/ do checkout principal."""
    from base_checagens import raiz_principal

    pasta = Path(dados or os.environ.get("CHECAGEM_DADOS") or raiz_principal() / "data")
    arquivo = pasta / "avaliacao" / "limiar.json"
    if not arquivo.exists():
        return None
    limiar = json.loads(arquivo.read_text(encoding="utf-8")).get("limiar")
    if limiar is not None and not (isinstance(limiar, (int, float)) and 0 <= limiar <= 1):
        raise ValueError(f"{arquivo}: limiar deve ser um número entre 0 e 1 ou null, veio {limiar!r}")
    return limiar


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
