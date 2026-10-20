"""Fechamento de fala: pedaços de áudio já transcritos e atribuídos viram falas fechadas.

Uma fala fecha quando muda quem fala ou depois de 60 s; a minutagem conta do 00:00 marcado pelo operador.
Os pedaços chegam em segundos da fonte (do arquivo ou desde o início da captura); as falas saem em segundos
desde o 00:00.
"""
from falas import Fechador, Pedaco


def test_quando_muda_quem_fala_a_fala_anterior_fecha():
    f = Fechador(zero=0.0)
    assert f.recebe(Pedaco("Lula", 1.0, 4.0, "Olha, pergunta simples")) == []
    assert f.recebe(Pedaco("Lula", 4.2, 7.0, "ao candidato Flávio.")) == []
    fechadas = f.recebe(Pedaco("Flávio", 7.5, 10.0, "Eu respondo."))
    assert [(x.quem, x.inicio_s, x.fim_s, x.texto) for x in fechadas] == [
        ("Lula", 1.0, 7.0, "Olha, pergunta simples ao candidato Flávio.")]


def resumo(falas):
    return [(x.quem, x.inicio_s, x.fim_s, x.texto) for x in falas]


def test_fala_de_mais_de_60_s_fecha_aos_60_s_e_continua_numa_nova():
    f = Fechador(zero=0.0)
    saidas = []
    for k in range(7):  # Lula fala 70 s seguidos, em pedaços de 10 s
        saidas += f.recebe(Pedaco("Lula", 10.0 * k, 10.0 * (k + 1), f"p{k}"))
    assert resumo(saidas) == [("Lula", 0.0, 60.0, "p0 p1 p2 p3 p4 p5")]
    assert resumo(f.recebe(Pedaco("Flávio", 71.0, 73.0, "Não."))) == [("Lula", 60.0, 70.0, "p6")]


def test_minutagem_conta_do_00_00_marcado_pelo_operador():
    f = Fechador()
    f.marcar_zero(15577.0)  # abertura do mediador, no segundo 15577 da gravação
    f.recebe(Pedaco("Mediação", 15578.5, 15583.25, "Boa noite."))
    assert resumo(f.recebe(Pedaco("Lula", 15584.0, 15590.0, "Boa noite."))) == [
        ("Mediação", 1.5, 6.25, "Boa noite.")]


def test_sem_00_00_marcado_nada_sai_e_a_marcacao_libera_so_o_que_termina_depois_dela():
    f = Fechador()
    assert f.recebe(Pedaco("Mediação", 10.0, 20.0, "Vinheta e apresentação.")) == []
    assert f.recebe(Pedaco("Lula", 30.0, 35.0, "Teste de som.")) == []
    assert f.recebe(Pedaco("Mediação", 40.0, 48.0, "Está começando o debate.")) == []
    # o operador marca o 00:00 com atraso: no segundo 32, que já passou
    assert resumo(f.marcar_zero(32.0)) == [("Lula", 0.0, 3.0, "Teste de som.")]
    assert resumo(f.recebe(Pedaco("Flávio", 49.0, 52.0, "Boa noite."))) == [
        ("Mediação", 8.0, 16.0, "Está começando o debate.")]


def test_fala_que_comeca_pouco_antes_do_00_00_sai_com_inicio_zero():
    f = Fechador()
    f.recebe(Pedaco("Mediação", 99.0, 104.0, "Boa noite, começa agora o debate."))
    f.marcar_zero(100.0)
    assert resumo(f.recebe(Pedaco("Lula", 105.0, 107.0, "Boa noite."))) == [
        ("Mediação", 0.0, 4.0, "Boa noite, começa agora o debate.")]


def test_silencio_longo_fecha_a_fala_em_andamento():
    f = Fechador(zero=0.0, silencio_s=5.0)
    f.recebe(Pedaco("Flávio", 10.0, 20.0, "Encerro aqui."))
    assert f.tempo(24.0) == []  # 4 s de silêncio: pode ser só uma pausa
    assert resumo(f.tempo(25.5)) == [("Flávio", 10.0, 20.0, "Encerro aqui.")]
    assert f.tempo(40.0) == []


def test_fim_da_fonte_fecha_a_fala_em_andamento():
    f = Fechador(zero=0.0)
    f.recebe(Pedaco("Lula", 10.0, 20.0, "Obrigado."))
    assert resumo(f.fim()) == [("Lula", 10.0, 20.0, "Obrigado.")]
    assert f.fim() == []


def test_pedaco_curto_demais_para_saber_quem_fala_fica_com_a_fala_em_volta():
    f = Fechador(zero=0.0)
    f.recebe(Pedaco("Lula", 0.0, 5.0, "O senhor votou contra."))
    assert f.recebe(Pedaco(None, 5.2, 5.6, "Contra.")) == []
    assert resumo(f.recebe(Pedaco("Flávio", 6.0, 9.0, "Não votei."))) == [
        ("Lula", 0.0, 5.6, "O senhor votou contra. Contra.")]
    # sem fala em andamento, o pedaço sem locutor fica com quem vier depois
    f.fim()
    assert f.recebe(Pedaco(None, 20.0, 20.4, "Bom.")) == []
    f.recebe(Pedaco("Lula", 20.5, 23.0, "Vamos lá."))
    assert resumo(f.fim()) == [("Lula", 20.0, 23.0, "Bom. Vamos lá.")]


def test_minutagem_sai_em_centesimos_de_segundo():
    f = Fechador(zero=15577.3)
    f.recebe(Pedaco("Lula", 15578.4, 15581.123456, "Boa noite."))
    assert resumo(f.fim()) == [("Lula", 1.1, 3.82, "Boa noite.")]
