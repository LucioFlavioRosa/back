"""AS QUATRO FASES DA OBRA, E O QUE NÃO PODE APARECER EM CADA UMA.

A lista de obras do modal do cronograma passou a trazer a linha do tempo:

    predecessoras → execução → espera até a cobrança → ramp-up da adesão

Duas regras não óbvias, e são elas que estes testes guardam:

1. `lag_meses` e `maturacao_meses` existem em TODA linha de `otim_obra`, mas só a
   obra-âncora de coleta os recebe do cadastro. Nas demais eles carregam o DEFAULT da
   classe `Obra` do motor — 1 e 2 —, que não é dado. Uma EEE não tem "tempo até a
   cobrança", e mostrar o default seria inventar um número que alguém soma.

2. O início das predecessoras é DERIVADO. `tempo_predecessoras` é um piso no motor
   ("não começa antes do mês N"), não uma janela agendada; a data sai ancorando o fim
   do intervalo no início da execução.
"""
import pytest

from app.infra.repositorios.nivel_detalhe import _fases, _mes_antes


def test_o_mes_anterior_atravessa_a_virada_do_ano():
    assert _mes_antes("2035-10", 7) == "2035-03"
    assert _mes_antes("2035-03", 7) == "2034-08"     # atravessa o ano
    assert _mes_antes("2026-01", 1) == "2025-12"


def test_sem_data_ou_sem_prazo_nao_inventa():
    assert _mes_antes(None, 7) is None
    assert _mes_antes("2035-10", None) == "2035-10"  # prazo zero/ausente: não desloca
    assert _mes_antes("2035-10", 0) == "2035-10"
    assert _mes_antes("lixo", 3) is None


LINHA = {
    "prazo_meses": 17,
    "prazo_inicio_meses": 7,
    "lag_meses": 1,
    "maturacao_meses": 2,
    "data_inicio": "2035-10",
    "data_inicio_faturamento": "2038-01",
}


def test_a_obra_de_coleta_traz_a_linha_do_tempo_inteira():
    f = _fases({**LINHA, "eh_coleta": True})
    assert f["mesesPredecessoras"] == 7
    assert f["inicioPredecessoras"] == "2035-03"     # 7 meses antes de começar
    assert f["prazoMeses"] == 17
    assert f["mesesAteCobranca"] == 1
    assert f["dataInicioFaturamento"] == "2038-01"
    assert f["mesesRampUp"] == 2


def test_a_obra_que_NAO_fatura_nao_mostra_cobranca_nem_ramp_up():
    """O caso que motivou a regra: na base, TODA EEE sai com `lag_meses=1` e
    `maturacao_meses=2` — o default da classe, não o cadastro."""
    f = _fases({**LINHA, "eh_coleta": False})
    assert f["mesesAteCobranca"] is None
    assert f["dataInicioFaturamento"] is None
    assert f["mesesRampUp"] is None
    # o que é dela continua saindo
    assert f["mesesPredecessoras"] == 7 and f["prazoMeses"] == 17
    assert f["inicioPredecessoras"] == "2035-03"


def test_obra_de_terceiro_sem_data_de_inicio_nao_ganha_predecessoras_inventadas():
    """Do terceiro o motor só calcula a conclusão — não há início de execução, e
    portanto não há de onde ancorar o intervalo."""
    f = _fases({**LINHA, "eh_coleta": False, "data_inicio": None})
    assert f["inicioPredecessoras"] is None
    assert f["mesesPredecessoras"] == 7      # a duração continua sendo dado da obra
