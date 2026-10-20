"""Processo de teste da retomada (test_noite_retomada.py): uma noite inteira com a linha falsa, o dublê
do Claude e o publicador num remoto local, para ser morta no meio e rodada de novo.

Uso: python -P tests/processo_noite.py PASTA_DA_SESSAO REPO_DO_SITE
Cada chamada ao dublê vai para PASTA/../chamadas.jsonl (etapa e processo)."""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path[:0] = [str(AQUI.parent / "scripts"), str(AQUI)]

from base_checagens import Indice  # noqa: E402
from falas import Fala  # noqa: E402
from noite import Noite  # noqa: E402
from publicador import Publicador  # noqa: E402
from sessao import BRASILIA  # noqa: E402
from test_sessao import (DESEMPREGO, FALA_DESEMPREGO, FALA_LICENCA, FALA_MEDIACAO, FALA_PIX,  # noqa: E402
                         FIXTURES, LICENCA_2022, PIX, afirmacao, pedido, resposta)

LICENCA = afirmacao("você foi o único deputado da Assembleia que votou contra a licença-maternidade",
                    "Flávio Bolsonaro votou contra a licença-maternidade de seis meses na Alerj.", "perene",
                    {LICENCA_2022: (0.95, "Mesmo voto.")})
MODELOS = [("Lula", FALA_DESEMPREGO, DESEMPREGO), ("Flávio", FALA_PIX, PIX), ("Lula", FALA_LICENCA, LICENCA),
           ("Mediação", FALA_MEDIACAO, None)]


def falas_da_noite(n=12):
    """n falas: as quatro de exemplo em rodízio, cada uma com um número na frente para serem distintas."""
    out = []
    for k in range(n):
        quem, texto, _ = MODELOS[k % len(MODELOS)]
        out.append(Fala(quem, 60.0 * k, 60.0 * k + 30, f"Fala {k + 1}. {texto}"))
    return out


class Claude:
    """Dublê lento (como a API de verdade): cada chamada leva `pausa` s e fica registrada."""

    def __init__(self, registro, pausa=0.25):
        self.registro, self.pausa = registro, pausa

    def __call__(self, etapa, prompt, conteudo, schema):
        with open(self.registro, "a", encoding="utf-8") as f:
            f.write(json.dumps({"etapa": etapa, "pid": os.getpid()}) + "\n")
        time.sleep(self.pausa)
        if pedido(schema) == "extracao":
            achadas = [a for _, texto, a in MODELOS if a and texto in conteudo]
            saida = {"afirmacoes": [{k: a[k] for k in ("trecho", "reformulacao", "tipo")} for a in achadas]}
        else:
            a = next(a for _, _, a in MODELOS if a and a["trecho"] in conteudo)
            saida = {"confirmadas": [{"url": u, "nota": n, "motivo": m} for u, (n, m) in a["confirma"].items()
                                     if u in conteudo]}
        return resposta(etapa, saida)


class Linha:
    def __init__(self, desde, pausa=0.3):
        self.falas = [f for f in falas_da_noite() if desde is None or f.fim_s > desde]
        self.pausa = pausa

    def marcar_zero(self, instante):
        pass

    def parar(self):
        pass

    def __iter__(self):
        for f in self.falas:
            time.sleep(self.pausa)
            yield f


def main():
    pasta, repo = Path(sys.argv[1]), sys.argv[2]
    indice = Indice(json.loads((FIXTURES / "base_sessao.json").read_text(encoding="utf-8"))["registros"])
    noite = Noite(pasta, indice, Linha, chamar=Claude(pasta.parent / "chamadas.jsonl"),
                  meta={"limiar_automatico": 0.9}, publicador=Publicador(repo, intervalo=0.5, espera_erro=0.2),
                  log=lambda *a: None)
    if not any(e["tipo"] == "inicio" for e in noite.sessao.eventos):  # o 00:00 foi há 20 min
        noite.marca_zero(datetime.now(BRASILIA) - timedelta(minutes=20))
    noite.comeca()
    noite.espera()
    noite.encerra()
    print("fim", flush=True)


if __name__ == "__main__":
    main()
