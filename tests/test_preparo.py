"""A lista "Pronto para começar" (scripts/preparo.py): o que a noite confere sozinha antes do Começar.
Conferências lentas (credenciais do site) rodam em segundo plano e aparecem como "verificando" até acabar;
as que mudam com o tempo (som, energia) são lidas a cada vez que a tela pergunta."""
import threading

from preparo import Item, Preparo, confere_publicador
from test_noite import ate
from test_publicador import remoto  # noqa: F401 - remoto é fixture


def test_conferencia_lenta_aparece_verificando_e_depois_com_o_resultado():
    libera = threading.Event()

    def credenciais():
        libera.wait(10)
        return {"estado": "ok", "t": "Site aceita envio"}

    contador = iter(range(100))
    preparo = Preparo([Item("credenciais", "Site", credenciais, lento=True),
                       Item("som", "Som", lambda: {"estado": "ok", "t": f"leitura {next(contador)}"})])

    [cred, som] = preparo.itens()
    assert (cred["id"], cred["estado"]) == ("credenciais", "verificando")
    assert som["t"] == "leitura 0"
    libera.set()
    ate(lambda: preparo.itens()[0]["estado"] == "ok")
    assert preparo.itens()[1]["t"] != preparo.itens()[1]["t"]  # o som é lido de novo a cada pergunta


def test_conferencia_que_quebra_vira_problema_com_a_mensagem():
    def quebra():
        raise OSError("disco cheio")

    preparo = Preparo([Item("base", "Base", quebra)])

    [item] = preparo.itens()
    assert item["estado"] == "problema" and "disco cheio" in item["s"]


def test_parar_solta_o_medidor_de_som():
    class Medidor:
        parado = False

        def parar(self):
            self.parado = True

    medidor = Medidor()
    Preparo([], medidor=medidor).parar()
    assert medidor.parado


def test_credenciais_do_site_conferidas_sem_enviar_nada(remoto):
    nu, repo = remoto

    ok, mensagem = confere_publicador(repo, "origin", "main")
    falhou, motivo = confere_publicador(repo, "nao-existe", "main")

    assert ok and "origin/main" in mensagem
    assert not falhou and "nao-existe" in motivo
