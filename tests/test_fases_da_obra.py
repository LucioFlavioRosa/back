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


def test_mes_fora_do_calendario_nao_vira_data_plausivel():
    """Achado pela revisao do Codex em 28/09/2026. A conversao ingenua aceitava
    '2035-13' e devolvia '2035-12' — uma data NORMAL e errada, que e o pior desfecho
    num numero de planejamento: ninguem desconfia dela."""
    assert _mes_antes("2035-13", 1) is None
    assert _mes_antes("2035-00", 1) is None
    assert _mes_antes("2035-1", 1) is None       # sem o zero a esquerda nao e o formato
    assert _mes_antes("35-10", 1) is None
    assert _mes_antes("2035-10-15", 1) is None   # data completa nao e mes


def test_antes_do_ano_zero_devolve_nada_em_vez_de_ano_negativo():
    """Com prazo maior que a ancora a conta caia em ano negativo e devolvia
    '-001-12' — texto que parece data e nao segue o contrato 'AAAA-MM'."""
    assert _mes_antes("0000-01", 1) is None
    assert _mes_antes("0001-01", 24) is None
    assert _mes_antes("2035-10", 50_000) is None
    # e o limite continua valendo
    assert _mes_antes("0000-02", 1) == "0000-01"


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


# ------------------------------------------------ o CAPEX do terreno, e a cobrança plena
from app.infra.repositorios.nivel_detalhe import _capex_terreno, _mes_depois


def test_a_cobranca_plena_e_o_fim_do_ramp_up():
    """O ramp-up COMEÇA com a cobrança e dura a maturação; o fim dele é a cobrança
    plena. São dois marcos de data, e o usuário confere os dois."""
    f = _fases({**LINHA, "eh_coleta": True})
    assert f["dataInicioFaturamento"] == "2038-01"
    assert f["mesesRampUp"] == 2
    assert f["dataCobrancaPlena"] == "2038-03"


def test_a_obra_que_nao_fatura_nao_tem_cobranca_plena():
    assert _fases({**LINHA, "eh_coleta": False})["dataCobrancaPlena"] is None


def test_o_mes_depois_atravessa_o_ano_e_recusa_o_que_nao_e_mes():
    assert _mes_depois("2029-12", 1) == "2030-01"
    assert _mes_depois("2029-01", 24) == "2031-01"
    assert _mes_depois("2029-13", 1) is None
    assert _mes_depois(None, 3) is None
    assert _mes_depois("2029-09", 0) == "2029-09"


def test_o_terreno_e_o_que_sobra_do_capex_alem_de_quantidade_vezes_preco():
    """Na ETE nova o CAPEX inclui o terreno, então `quantidade × preço` não fecha
    sozinho. O resíduo É o terreno, e sai em coluna própria para a conta fechar:
    3 módulos × R$ 500.000 + R$ 300.000 = R$ 1.800.000."""
    assert _capex_terreno({"quantidade": 3, "preco_unitario": 500_000, "capex": 1_800_000}) == 300_000


def test_onde_a_conta_ja_fecha_o_terreno_sai_nulo():
    """Numa rede coletora não há parcela além do unitário — a coluna fica vazia em vez
    de abrir uma coluna de zeros."""
    assert _capex_terreno({"quantidade": 2173.08, "preco_unitario": 392.11, "capex": 852086.3988}) is None
    assert _capex_terreno({"quantidade": None, "preco_unitario": 1, "capex": 1}) is None
