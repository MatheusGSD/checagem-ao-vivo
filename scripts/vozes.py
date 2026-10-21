"""Perfis de voz de Lula e Flávio e reconhecimento de quem fala num trecho de áudio (o que roda ao vivo).

Os perfis são a média dos embeddings ECAPA (o mesmo modelo do diarize.py) das janelas de ~3 s do
confronto livre da Band, cada uma rotulada pela fala de referência (bloco1_final.json) que a cobre.
Um trecho vai para o perfil com maior cosseno; abaixo do limiar, vai para "Mediação" (mediação,
jornalistas e qualquer outra voz). O cálculo dos perfis e a medição do limiar ficam em vozes_medir.py.

Uso como biblioteca (ao vivo, ticket 09):
    from vozes import Reconhecedor
    r = Reconhecedor.carregar()          # perfis e limiar de <dados>/vozes/perfis.json; carrega o ECAPA
    v = r.quem_fala(sinal)               # sinal: np.float32 mono 16 kHz, um trecho de fala
    v.quem                               # "Lula", "Flávio", "Mediação" ou None (trecho < 0,5 s)

<dados>: CHECAGEM_DADOS ou data/ do checkout principal (comum.pasta_dados).

Linha de comando (o mesmo que vozes_medir.py):
    python -P scripts/vozes.py perfis    # grava <dados>/vozes/perfis.json
    python -P scripts/vozes.py medir     # mede no debate todo, escolhe o limiar, grava relatorio.md
"""
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

TAXA = 16000
MEDIACAO = "Mediação"  # classe "Mediação/outros": nenhuma voz de candidato parecida o bastante
MINIMO_S = 0.5  # abaixo disso o ECAPA não dá embedding confiável (mesmo corte do diarize.py)
MODELO = "speechbrain/spkrec-ecapa-voxceleb"


def pasta_vozes():
    """<dados>/vozes. A importação fica aqui dentro: quem só grava (notebook reserva) copia falas.py
    e vozes.py, sem o resto dos scripts."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from comum import pasta_dados

    return pasta_dados() / "vozes"


@dataclass
class Veredito:
    quem: str | None
    semelhanca: float  # cosseno com o perfil mais parecido
    semelhancas: dict  # cosseno com cada perfil


def _unitario(v):
    v = np.asarray(v, dtype=np.float64).ravel()
    return v / np.linalg.norm(v)


def codificador_ecapa(threads=None):
    """Devolve a função sinal -> embedding ECAPA (192 dim). Carrega o modelo do cache local, na CPU."""
    import torch
    from speechbrain.inference.speaker import EncoderClassifier
    from speechbrain.utils.fetching import LocalStrategy

    if threads:
        torch.set_num_threads(threads)  # vale para o processo inteiro
    enc = EncoderClassifier.from_hparams(source=MODELO, savedir=str(Path(tempfile.gettempdir()) / "ecapa"),
                                         local_strategy=LocalStrategy.COPY, run_opts={"device": "cpu"})

    def codifica(sinal):
        with torch.no_grad():
            x = torch.from_numpy(np.ascontiguousarray(sinal, dtype=np.float32)).unsqueeze(0)
            return enc.encode_batch(x).squeeze().cpu().numpy()

    return codifica


class Reconhecedor:
    """Diz quem fala num trecho: o perfil de maior cosseno, ou Mediação se ficar abaixo do limiar."""

    def __init__(self, perfis, limiar, codificador):
        self.perfis = {k: _unitario(v) for k, v in perfis.items()}
        self.limiar = float(limiar)
        self.codificador = codificador

    @classmethod
    def carregar(cls, pasta=None, limiar=None, codificador=None, threads=None):
        """Perfis e limiar de <pasta>/perfis.json (padrão: <dados>/vozes)."""
        d = json.loads((Path(pasta or pasta_vozes()) / "perfis.json").read_text(encoding="utf-8"))
        limiar = d["limiar"] if limiar is None else limiar
        if limiar is None:
            raise ValueError("perfis.json sem limiar: rode `vozes_medir.py medir` ou passe limiar=")
        return cls(d["perfis"], limiar, codificador or codificador_ecapa(threads))

    def embedding(self, sinal):
        return _unitario(self.codificador(sinal))

    def decide(self, embedding):
        sims = {k: float(p @ embedding) for k, p in self.perfis.items()}
        melhor = max(sims, key=sims.get)
        quem = melhor if sims[melhor] >= self.limiar else MEDIACAO
        return Veredito(quem, sims[melhor], sims)

    def quem_fala(self, sinal):
        if len(sinal) < MINIMO_S * TAXA:
            return Veredito(None, float("nan"), {})
        return self.decide(self.embedding(sinal))


def gravar_perfis(pasta, perfis, limiar=None, extra=None):
    """Grava <pasta>/perfis.json no formato que Reconhecedor.carregar lê."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    d = {"modelo": MODELO, "limiar": limiar, "classes": [*perfis, MEDIACAO],
         "perfis": {k: [round(float(x), 7) for x in _unitario(v)] for k, v in perfis.items()}, **(extra or {})}
    (pasta / "perfis.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from vozes_medir import main

    main()
