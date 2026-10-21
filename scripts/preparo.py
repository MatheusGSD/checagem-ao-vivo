"""A lista "Pronto para começar" da tela do operador: o que a noite confere sozinha antes do Começar.

Cada item tem um estado (ok, atencao, problema, neutro ou verificando), um título curto (t) e uma linha de
detalhe (s). Conferências lentas (credenciais do site, leitura da base) rodam uma vez em segundo plano e
aparecem como "verificando" até acabar; as que mudam com o tempo (som, energia) são lidas a cada pergunta
da tela. Nada aqui grava, transcreve ou envia: o medidor de som só lê o nível do loopback e solta o
dispositivo antes de a captura de verdade começar (Preparo.parar, chamado pela noite no Começar).

Em código: Preparo(itens_da_noite(...), medidor=MedidorDeSom()) -> Noite(preparo=...). Ver noite.py.
"""
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comum import BRASILIA  # noqa: E402


@dataclass
class Item:
    id: str
    nome: str
    confere: Callable[[], dict]  # -> {"estado", "t", "s"?, ...}
    lento: bool = False  # roda uma vez, em segundo plano; senão, a cada pergunta da tela


class Preparo:
    def __init__(self, itens, medidor=None):
        self._itens, self.medidor = list(itens), medidor
        self._resultados = {}
        for item in self._itens:
            if item.lento:
                threading.Thread(target=self._confere_lento, args=(item,), name=f"preparo-{item.id}",
                                 daemon=True).start()

    def _confere_lento(self, item):
        self._resultados[item.id] = _confere(item)

    def itens(self):
        out = []
        for item in self._itens:
            if item.lento:
                r = self._resultados.get(item.id) or {"estado": "verificando", "t": "Verificando…"}
            else:
                r = _confere(item)
            out.append({"id": item.id, "nome": item.nome, **r})
        return out

    def parar(self):
        if self.medidor:
            self.medidor.parar()


def _confere(item):
    try:
        return item.confere()
    except (Exception, SystemExit) as e:  # noqa: BLE001 - vira um item vermelho, não derruba a tela
        return {"estado": "problema", "t": "Não deu para conferir", "s": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------- som

class MedidorDeSom:
    """Nível do som (dBFS do último segundo) no loopback WASAPI da saída padrão, sem gravar nada. O
    loopback não entrega pacotes enquanto nada toca: sem pacote há mais de 1 s, o nível é silêncio."""

    def __init__(self, dispositivo=None):
        self.dispositivo, self.nome, self.erro = dispositivo, None, None
        self._energia = deque()  # (amostras, soma dos quadrados), do último segundo
        self._taxa, self._ultimo = None, None
        self._trava = threading.Lock()
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._roda, name="medidor-de-som", daemon=True)
        self._thread.start()

    def _roda(self):
        try:
            import numpy as np
            import pyaudiowpatch as pyaudio

            pa = pyaudio.PyAudio()
            try:
                dev = pa.get_default_wasapi_loopback() if self.dispositivo is None \
                    else pa.get_device_info_by_index(self.dispositivo)
                self.nome, self._taxa = dev["name"], int(dev["defaultSampleRate"])
                canais = int(dev["maxInputChannels"])

                def callback(dados, n, info, status):
                    x = np.frombuffer(dados, dtype=np.int16).astype(np.float32) / 32768
                    with self._trava:
                        self._energia.append((len(x) // max(1, canais), float(np.dot(x, x)) / max(1, canais)))
                        while sum(k for k, _ in self._energia) - self._energia[0][0] >= self._taxa:
                            self._energia.popleft()
                        self._ultimo = time.monotonic()
                    return (None, pyaudio.paContinue)

                stream = pa.open(format=pyaudio.paInt16, channels=canais, rate=self._taxa, input=True,
                                 input_device_index=dev["index"], frames_per_buffer=self._taxa // 20,
                                 stream_callback=callback)
                self._parar.wait()
                stream.stop_stream()
                stream.close()
            finally:
                pa.terminate()
        except Exception as e:  # noqa: BLE001 - vai para a lista
            self.erro = f"{type(e).__name__}: {e}"

    def nivel_db(self):
        """dBFS do último segundo (piso -90), -90 se nada toca, None antes de abrir o dispositivo."""
        import math

        with self._trava:
            if self._ultimo is None:
                return None if self.nome is None else -90.0
            if time.monotonic() - self._ultimo > 1.0:
                return -90.0
            n = sum(k for k, _ in self._energia)
            rms = (sum(e for _, e in self._energia) / n) ** 0.5 if n else 0.0
        return round(max(-90.0, 20 * math.log10(rms)) if rms > 0 else -90.0, 1)

    def parar(self):
        self._parar.set()
        self._thread.join(5)


SILENCIO_DB = -55.0  # o mesmo corte da faixa de estado da tela


def item_som(medidor):
    def confere():
        if medidor.erro:
            return {"estado": "problema", "t": "Sem acesso ao som do computador",
                    "s": f"Confira a saída de som padrão do Windows. Detalhe: {medidor.erro}"}
        if medidor.nome is None:
            return {"estado": "verificando", "t": "Abrindo o dispositivo de som…"}
        db = medidor.nivel_db()
        tocando = db is not None and db > SILENCIO_DB
        return {"estado": "ok" if tocando else "atencao", "nivel_db": db,
                "t": f"Captura: {medidor.nome}",
                "s": "Som chegando. É o g1?" if tocando else
                     "Silêncio agora. Dê play no g1 (som no máximo, sem mudo) e veja o medidor subir."}
    return Item("som", "Som do computador", confere)


# ---------------------------------------------------------------- base, chave, automática, avisos

def item_base(pasta_base, n_checagens, atualiza=True):
    def confere():
        atualizado = json.loads((Path(pasta_base) / "base.json").read_text(encoding="utf-8")).get("atualizado_em")
        quando = datetime.fromisoformat(atualizado) if atualizado else None
        if quando and quando.tzinfo is None:
            quando = quando.replace(tzinfo=BRASILIA)
        idade_h = (datetime.now(BRASILIA) - quando).total_seconds() / 3600 if quando else None
        texto = f"{n_checagens:,}".replace(",", ".") + " checagens"
        s = f"atualizada em {quando.astimezone(BRASILIA):%d/%m às %H:%M}" if quando else "sem data da última atualização"
        if idade_h is not None and idade_h > 24:
            return {"estado": "atencao", "t": f"Base: {texto}",
                    "s": f"{s}. Mais de um dia: " + ("a noite atualiza ao começar." if atualiza
                                                     else "rode base_checagens.py atualizar.")}
        return {"estado": "ok", "t": f"Base: {texto}",
                "s": s + (". A noite atualiza a cada 10 min." if atualiza else ". Sem atualização nesta sessão (--sem-base).")}
    return Item("base", "Base de checagens", confere, lento=True)


def item_chave_claude():
    def confere():
        if os.environ.get("ANTHROPIC_API_KEY"):
            return {"estado": "ok", "t": "Chave do Claude presente", "s": "ANTHROPIC_API_KEY (no .env ou no ambiente)"}
        return {"estado": "problema", "t": "Falta a chave do Claude",
                "s": "Ponha ANTHROPIC_API_KEY no .env da pasta do projeto e abra o programa de novo."}
    return Item("claude", "Chave do Claude", confere)


def item_automatica(limiar, motivo):
    def confere():
        if limiar is None:
            return {"estado": "neutro", "t": "Publicação automática desligada",
                    "s": "Toda ligação passa por você." + (f" Motivo: {motivo}" if motivo else "")}
        return {"estado": "ok", "t": "Publicação automática ligada",
                "s": f"Sai sozinha a nota ≥ {str(limiar).replace('.', ',')}, 10 min depois do 00:00."}
    return Item("automatica", "Publicação automática", confere)


def item_aviso(k, texto):
    return Item(f"aviso-{k}", "Aviso", lambda: {"estado": "atencao", "t": "Atenção", "s": texto})


# ---------------------------------------------------------------- site

def confere_publicador(repo, remoto="origin", ramo="main", timeout=90):
    """Busca o ramo e simula um push (git push --dry-run), como `publicador.py verifica`, mas sem
    terminal: devolve (ok, mensagem). Nada é enviado."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for args in (["fetch", "--no-tags", remoto, f"+refs/heads/{ramo}:refs/remotes/{remoto}/{ramo}"],
                 ["push", "--dry-run", remoto, f"refs/remotes/{remoto}/{ramo}:refs/heads/{ramo}"]):
        try:
            r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return False, f"git {args[0]} sem resposta em {timeout} s ({remoto}/{ramo})"
        except OSError as e:
            return False, f"git {args[0]}: {e}"
        if r.returncode:
            erro = (r.stderr or r.stdout).decode("utf-8", "replace").strip().splitlines()
            erro = " | ".join(l for l in erro if l.strip() and not l.startswith("hint:"))[-300:]
            return False, f"git {args[0]} para {remoto}/{ramo} falhou: {erro or 'sem mensagem'}"
    return True, f"{remoto}/{ramo} aceita envio (simulado, nada foi enviado)"


def item_credenciais(repo, remoto, ramo, destino):
    def confere():
        ok, mensagem = confere_publicador(repo, remoto, ramo)
        if ok:
            return {"estado": "ok", "t": "Site aceita envio" if destino == "real" else "Cópia local aceita envio",
                    "s": mensagem}
        acao = ("Resolva o login do git (Git Credential Manager) antes das 21h: rode publicador.py verifica."
                if destino == "real" else "A cópia local do site está com problema: abra o Ensaio do zero.")
        return {"estado": "problema", "t": "O site não aceita envio", "s": f"{acao} Detalhe: {mensagem}"}
    return Item("credenciais", "Credenciais do site", confere, lento=True)


# ---------------------------------------------------------------- energia (Windows)

MODOS_DE_ENERGIA = {  # PowerGetEffectiveOverlayScheme: o "Modo de energia" de Configurações > Energia
    "ded574b5-45a0-4f42-8737-46345c09c238": ("Melhor desempenho", "ok"),
    "00000000-0000-0000-0000-000000000000": ("Equilibrado", "atencao"),
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": ("Melhor desempenho (intermediário)", "atencao"),
    "961cc777-2547-4f9d-8174-7d86181b8a7a": ("Melhor eficiência de energia", "atencao"),
}


def le_energia():
    """(na_tomada: bool | None, bateria_%: int | None, modo: str | None). None onde não dá para saber."""
    if sys.platform != "win32":
        return None, None, None
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("ac", ctypes.c_ubyte), ("bandeira", ctypes.c_ubyte), ("pct", ctypes.c_ubyte),
                    ("economia", ctypes.c_ubyte), ("vida", ctypes.c_ulong), ("vida_total", ctypes.c_ulong)]

    class Guid(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

    tomada = pct = modo = None
    st = Status()
    if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        tomada = {0: False, 1: True}.get(st.ac)
        pct = st.pct if st.pct <= 100 and st.bandeira != 128 else None  # 128: sem bateria
    try:
        g = Guid()
        if ctypes.windll.powrprof.PowerGetEffectiveOverlayScheme(ctypes.byref(g)) == 0:
            modo = f"{g.d1:08x}-{g.d2:04x}-{g.d3:04x}-{bytes(g.d4[:2]).hex()}-{bytes(g.d4[2:]).hex()}"
    except (AttributeError, OSError):
        pass
    return tomada, pct, modo


def item_energia(le=le_energia):
    def confere():
        tomada, pct, modo = le()
        nome, estado_modo = MODOS_DE_ENERGIA.get(modo, (None, "neutro"))
        modo_txt = f"Modo de energia: {nome}" if nome else "Modo de energia: não detectado"
        if tomada is False:
            return {"estado": "problema", "t": f"Na bateria{f' ({pct}%)' if pct is not None else ''}",
                    "s": f"Ligue na tomada: na bateria a transcrição atrasa mais de 1 min. {modo_txt}."}
        if tomada is None and nome is None:
            return {"estado": "neutro", "t": "Energia: não detectada", "s": "Confira: na tomada e em Melhor desempenho."}
        if estado_modo != "ok":
            return {"estado": "atencao", "t": "Na tomada" if tomada else "Energia",
                    "s": f"{modo_txt}. Mude para Melhor desempenho (Configurações > Energia)."}
        return {"estado": "ok", "t": "Na tomada, em Melhor desempenho" if tomada else "Melhor desempenho",
                "s": "Plano de energia certo para a noite."}
    return Item("energia", "Energia", confere)
