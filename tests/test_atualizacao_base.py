"""Atualização da base durante o debate: falhas da API do Google e do RSS do Fato ou Fake chegam a quem
chamou (a tela de aprovação), em vez de sumirem no log da coleta, e uma não derruba a outra."""
import json

import requests

from base_checagens import API, atualiza

CLAIM = {"text": "O Pix foi criado no governo Bolsonaro", "claimant": "Redes sociais", "claimDate": "2026-10-23T10:00:00Z",
         "claimReview": [{"publisher": {"name": "Aos Fatos", "site": "aosfatos.org"},
                          "url": "https://www.aosfatos.org/noticias/pix-criacao-2026/", "title": "Pix",
                          "reviewDate": "2026-10-23T12:00:00Z", "textualRating": "Falso", "languageCode": "pt"}]}


class Resposta:
    def __init__(self, codigo, corpo=None):
        self.status_code, self._corpo = codigo, corpo or {}

    def json(self):
        return self._corpo


def internet(google, g1=None):
    """requests.Session.get falso: google = {busca: Resposta}; g1(url) responde ao g1 (padrão: fora do ar)."""
    def get(self, url, params=None, timeout=None, **kw):
        if url == API:
            return google[params["query"]]
        if g1 is None:
            raise requests.ConnectionError("g1 fora do ar")
        return g1(url)
    return get


def test_buscas_do_google_que_falham_e_g1_fora_do_ar_vao_para_ao_erro_e_o_resto_entra_na_base(tmp_path, monkeypatch):
    monkeypatch.setattr(requests.Session, "get", internet(
        {"Lula": Resposta(503), "Flávio": Resposta(200, {"claims": [CLAIM]}), "Pix": Resposta(403)}))
    erros = []

    novos = atualiza(tmp_path, ["Lula", "Flávio", "Pix"], "chave", pausa=0, pausa_google=0, log=lambda *a: None,
                     ao_erro=erros.append)

    assert [r["agencia"] for r in novos] == ["Aos Fatos"]  # o que veio do Google entra mesmo com o g1 fora
    assert len(json.loads((tmp_path / "base.json").read_text(encoding="utf-8"))["registros"]) == 1
    assert erros == ["API do Google: 2 de 3 buscas falharam (último: HTTP 403 ('Pix', página 0))",
                     "RSS do Fato ou Fake: ConnectionError: g1 fora do ar"]


def test_atualizacao_sem_falha_nao_avisa_nada(tmp_path, monkeypatch):
    monkeypatch.setattr("base_checagens.coleta_fof_rss", lambda g1, saida: 1)
    monkeypatch.setattr("base_checagens.G1", lambda **kw: None)
    monkeypatch.setattr(requests.Session, "get", internet({"Lula": Resposta(200, {"claims": []})}))
    erros = []

    atualiza(tmp_path, ["Lula"], "chave", pausa=0, pausa_google=0, log=lambda *a: None, ao_erro=erros.append)

    assert erros == []
