"""Do áudio às falas: captura, detecção de voz, transcrição em trechos, quem fala e fechamento de fala."""
from dataclasses import dataclass

MEDIACAO = "Mediação"  # o mesmo nome de vozes.MEDIACAO (sem importar o vozes, que puxa o torch)


@dataclass
class Pedaco:
    """Trecho transcrito e atribuído, em segundos da fonte."""
    quem: str | None
    inicio: float
    fim: float
    texto: str


@dataclass
class Fala:
    """Fala fechada, em segundos desde o 00:00."""
    quem: str
    inicio_s: float
    fim_s: float
    texto: str


class Fechador:
    def __init__(self, zero=None, max_s=60.0, silencio_s=None):
        self.zero = zero
        self.max_s = max_s
        self.silencio_s = silencio_s
        self._atual = []
        self._retidas = []  # fechadas antes de o 00:00 ser marcado, em segundos da fonte

    def marcar_zero(self, t):
        self.zero = t
        retidas, self._retidas = self._retidas, []
        return [f for r in retidas if (f := self._relativa(r))]

    def recebe(self, p):
        fechadas = []
        quem = self._quem()
        muda = p.quem is not None and quem is not None and p.quem != quem
        if self._atual and (muda or p.fim - self._atual[0].inicio > self.max_s):
            fechadas += self._fecha()
        self._atual.append(p)
        return fechadas

    def tempo(self, t):
        if self._atual and self.silencio_s is not None and t - self._atual[-1].fim >= self.silencio_s:
            return self._fecha()
        return []

    def fim(self):
        return self._fecha() if self._atual else []

    def _quem(self):
        return next((p.quem for p in self._atual if p.quem is not None), None)

    def _fecha(self):
        quem = self._quem() or MEDIACAO  # só pedaços curtos demais: voz não reconhecida
        ps, self._atual = self._atual, []
        bruta = (quem, ps[0].inicio, ps[-1].fim, " ".join(p.texto.strip() for p in ps if p.texto.strip()))
        if self.zero is None:
            self._retidas.append(bruta)
            return []
        return [f] if (f := self._relativa(bruta)) else []

    def _relativa(self, bruta):
        """Em segundos desde o 00:00; None para a fala que terminou antes dele."""
        quem, ini, fim, texto = bruta
        if fim <= self.zero:
            return None
        return Fala(quem, round(max(0.0, ini - self.zero), 2), round(fim - self.zero, 2), texto)
