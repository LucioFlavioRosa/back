"""O TETO da sensibilidade — o número que se dá ANTES de gastar solver.

O que se prende aqui é a única propriedade que faz o teto valer alguma coisa:
**ele nunca pode ficar abaixo do que o otimizador conseguiria**. Um teto baixo
demais faz descartar uma análise que valia a pena, e o erro é invisível — ninguém
roda a simulação para descobrir que o aviso estava errado.
"""

import pytest

from app.dominio.teto import Candidata, DEGRAUS, teto


def c(nome: str, capex: float, vazao: float) -> Candidata:
    return Candidata(nome, capex, vazao)


ORCAMENTO = 1_000.0  # +10% = 100 de folga


def test_a_contagem_compra_as_mais_baratas():
    # 100 de folga compram a de 30 e a de 60 (90), e nao a de 100 sozinha.
    r = teto([c("a", 100, 1), c("b", 30, 1), c("d", 60, 1)], ORCAMENTO, [10])
    assert r["degraus"][0]["subbaciasNoMaximo"] == 2


def test_sem_folga_para_a_mais_barata_o_teto_e_zero():
    # E o caso que responde a pergunta de verdade: "nem no melhor caso da".
    r = teto([c("a", 500, 9)], ORCAMENTO, [10])
    assert r["degraus"][0]["subbaciasNoMaximo"] == 0
    # A vazao NAO e zero: a mochila fracionaria compra 1/5 da obra, e esse
    # numero e o teto honesto — nao ha solucao inteira que renda mais que isso.
    assert r["degraus"][0]["vazaoNoMaximo"] == pytest.approx(9 * 100 / 500)


def test_a_vazao_ordena_por_eficiencia_e_nao_por_preco():
    # A barata rende 1 por real; a cara rende 5. Com 100 de folga, o teto de
    # vazao e comprar a cara (100 -> 500), e nao as baratas (100 -> 100).
    caras = [c("cara", 100, 500)] + [c(f"b{i}", 10, 10) for i in range(10)]
    r = teto(caras, ORCAMENTO, [10])
    assert r["degraus"][0]["vazaoNoMaximo"] == pytest.approx(500)
    # E a CONTAGEM continua respondendo a outra pergunta: dez baratas cabem.
    assert r["degraus"][0]["subbaciasNoMaximo"] == 10


def test_sub_bacia_sem_capex_proprio_entra_a_custo_zero():
    # ELA CONTA, e o teto que a excluia ficava ABAIXO do alcancavel — deixando de
    # ser teto. No problema RELAXADO (sem precedencia, sem ETE, sem janela) ela
    # custa zero e cabe sempre; o que a prende no problema real e exatamente o
    # que a relaxacao joga fora.
    r = teto([c("presa", 0, 999), c("paga", 50, 1)], ORCAMENTO, [10])
    assert r["degraus"][0]["subbaciasNoMaximo"] == 2
    assert r["degraus"][0]["vazaoNoMaximo"] == pytest.approx(1000)
    # E ela continua nomeada a parte, porque a leitura "estas nao dependem de
    # orcamento" e resposta: quem quiser essas mexe em precedencia, nao em verba.
    assert r["subbaciasFora"] == 2
    assert r["subbaciasSemCapexProprio"] == 1
    assert r["vazaoTotalPresa"] == pytest.approx(1000)


def test_a_de_graca_nao_consome_a_folga_das_pagas():
    # O erro simetrico: dar-lhe custo zero mas deixa-la ocupar lugar na fila do
    # dinheiro faria as pagas caberem em numero menor.
    r = teto([c("presa", 0, 5), c("a", 40, 1), c("b", 40, 1), c("d", 40, 1)], ORCAMENTO, [10])
    # 100 de folga compram DUAS de 40; a de graca entra por fora -> 3.
    assert r["degraus"][0]["subbaciasNoMaximo"] == 3


def test_o_teto_nunca_cai_quando_o_degrau_sobe():
    candidatas = [c(f"s{i}", 10 * (i + 1), 100 - i) for i in range(20)]
    r = teto(candidatas, ORCAMENTO, list(DEGRAUS))
    contagens = [d["subbaciasNoMaximo"] for d in r["degraus"]]
    vazoes = [d["vazaoNoMaximo"] for d in r["degraus"]]
    assert contagens == sorted(contagens)
    assert vazoes == sorted(vazoes)


def test_a_vazao_do_teto_domina_qualquer_carteira_viavel():
    # A propriedade que faz o numero ser um TETO, e nao mais uma solucao: sortear
    # carteiras que cabem no orcamento e conferir que nenhuma passa do teto.
    import random

    rnd = random.Random(7)
    candidatas = [c(f"s{i}", rnd.uniform(5, 90), rnd.uniform(1, 200)) for i in range(40)]
    # Tres sem capex proprio no meio: sao elas que a versao anterior excluia, e
    # e por isso que uma carteira viavel conseguia PASSAR do "teto".
    candidatas += [c(f"z{i}", 0.0, rnd.uniform(1, 200)) for i in range(3)]
    limite = teto(candidatas, ORCAMENTO, [30])["degraus"][0]["vazaoNoMaximo"]
    contagem = teto(candidatas, ORCAMENTO, [30])["degraus"][0]["subbaciasNoMaximo"]
    folga = ORCAMENTO * 0.30

    for _ in range(300):
        carteira = [x for x in candidatas if rnd.random() < 0.15]
        if sum(x.capex for x in carteira) <= folga:
            assert sum(x.vazao for x in carteira) <= limite + 1e-6
            # A MESMA propriedade vale para a contagem, e sem esta linha o vies
            # para baixo passava despercebido: a carteira gratuita cabia e o
            # "teto" de contagem ficava menor que ela.
            assert len(carteira) <= contagem


def test_folga_e_o_dinheiro_a_mais_e_nao_o_orcamento_novo():
    # +10% de 1000 sao 100 a mais, e nao 1100. Trocar os dois faria o teto
    # comprar o plano inteiro de novo.
    r = teto([c("a", 1, 1)], ORCAMENTO, [10, 50])
    assert [d["folga"] for d in r["degraus"]] == [100.0, 500.0]
