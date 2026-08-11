"""`capex` é derivado — uma conta só, e nenhum valor inventado.

A coluna vivia em estado misto: a carga da planilha gravava o valor arredondado
da origem, e o `PUT` de ficha regravava `quantidade × preco_unitario` com
precisão cheia. Duas fontes para o mesmo número, e nenhuma delas era a que a
simulação usava.

Quem decidiu foi o motor, e antes desta suíte: `otimizador_capex_v62.py:1165`
lê a decomposição e a faz prevalecer sobre a coluna, avisando quando as duas
discordam. Estes testes prendem o cadastro nessa mesma regra — e a constraint
`capex_e_derivado` (`migracoes/005_capex_derivado.sql`) a prende no banco, para
o dia em que alguém gravar por fora.

O que mudou de comportamento e merece teste próprio: o `or 0` que estava na
multiplicação. Ele transformava fator ausente em CAPEX zero — um número que
ninguém digitou, com cara de cadastro preenchido, numa obra que na verdade não
tem quantidade. Falta de fator agora vira nulo, que é o que se sabe.
"""

import pytest

from app.infra.repositorios.cadastro_escrita import ValorInvalido, _capex


def test_capex_e_quantidade_vezes_preco():
    assert _capex({"qtd": "100", "preco": "2.000,00"}) == 200000


def test_pt_br_com_decimal_e_a_forma_que_chega_da_tela():
    """`69,64 × 2.941,79` — a ficha `b1b25_1_1`, que motivou o item do plano."""
    assert _capex({"qtd": "69,64", "preco": "2.941,79"}) == pytest.approx(204866.2556)


def test_fator_ausente_e_nulo_e_nao_zero():
    """Zero afirmaria "esta obra não custa nada". Nulo diz que não dá para saber.

    A falta em si não passa despercebida: `quantidade` vazia é pendência
    (`pendencias.py:_OBRA`) e trava a simulação da unidade inteira. A régua é
    aquela — aqui só não se inventa o número.
    """
    assert _capex({"qtd": None, "preco": "2.000,00"}) is None
    assert _capex({"qtd": "100", "preco": None}) is None
    assert _capex({}) is None
    assert _capex({"qtd": "", "preco": "2.000,00"}) is None  # branco é ausência


def test_zero_de_verdade_continua_zero():
    """Quantidade zero é resposta, não silêncio: a obra existe e não tem extensão."""
    assert _capex({"qtd": "0", "preco": "1.200,00"}) == 0


def test_numero_torto_e_422_e_nao_500():
    """Antes estourava `TypeError` na multiplicação, e o usuário via erro genérico."""
    with pytest.raises(ValorInvalido) as e:
        _capex({"qtd": "cem", "preco": "2.000,00"})
    assert "obra.qtd" in str(e.value)

    with pytest.raises(ValorInvalido) as e:
        _capex({"qtd": "100", "preco": "dois mil"})
    assert "obra.preco" in str(e.value)


def test_o_corpo_nao_opina_sobre_o_capex():
    """`capex` no payload é ignorado — não há segunda opinião a considerar.

    Não é detalhe de implementação: é o que impede a tela de gravar um CAPEX que
    a simulação não usaria. `_OBRA` não mapeia o campo, e o cálculo só olha os
    dois fatores.
    """
    assert _capex({"qtd": "2", "preco": "3", "capex": "999.999,00"}) == 6


def test_a_tolerancia_da_constraint_cobre_o_arredondamento_da_planilha():
    """Um centavo não é gosto: arredondar a 2 casas erra no máximo meio centavo.

    As 205 linhas que divergiam no banco erravam exatamente R$ 0,005 — a origem
    guarda `1.184.928,56` onde a conta dá `1.184.928,555`. A constraint tem de
    deixar a carga da planilha passar e ainda assim recusar outro valor.
    """
    derivado = _capex({"qtd": "532,05", "preco": "2.227,10"})
    assert abs(derivado - 1184928.56) <= 0.01  # o que a planilha traz: passa
    assert abs(derivado - 1184920.00) > 0.01  # outro valor: recusado
