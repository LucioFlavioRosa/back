"""O TETO DA SENSIBILIDADE — quanto, no máximo, mais dinheiro pode comprar.

Responde antes de qualquer simulação: *com +10% de orçamento, quantas das
sub-bacias que ficaram de fora podem entrar, no melhor caso imaginável?* Se a
resposta for "duas de mil e noventa e nove", ninguém precisa gastar cinco
execuções de solver para descobrir que a curva é plana.

## É um TETO, e a palavra é literal

O número sai de um problema deliberadamente MAIS FÁCIL que o real: mantém só a
restrição de dinheiro e joga fora precedência, capacidade de ETE, janela de meta
e a divisão do orçamento por ano. Relaxar restrição só pode aumentar o ótimo,
então o valor daqui é sempre maior ou igual ao que o otimizador conseguiria — e é
por isso que ele serve para DESCARTAR ("nem no melhor caso dá") e nunca para
prometer ("vai dar isto").

Um teto honesto tem de errar sempre para o mesmo lado. As duas simplificações do
custo erram para cá também: obras de tronco compartilhadas entram uma vez só (o
custo real de trazer duas sub-bacias que dependem do mesmo tronco é menor do que
duas vezes o tronco, então contar por sub-bacia SUBESTIMA o custo agregado), e o
orçamento extra é tratado como um bolo único em vez de uma parcela por ano.

## Duas perguntas, duas ordenações, e a diferença importa

"Quantas cabem" e "quanta vazão cabe" não têm a mesma resposta, e usar uma
ordenação para as duas daria um número errado numa das pontas:

- **Quantas cabem** é maximizar a CONTAGEM sob orçamento. A ordem exata é da mais
  barata para a mais cara — e este é o ótimo de verdade, não um limite.
- **Quanta vazão cabe** é uma mochila. A ordem é por vazão POR real gasto, e o
  último item entra fracionado. Isso é o limite da relaxação linear da mochila:
  maior ou igual ao ótimo inteiro, que é exatamente o que um teto deve ser.

## Sub-bacia sem CAPEX próprio ENTRA, e a custo zero

Quem não fatura e não tem obra não construída no próprio nó está presa por outra
coisa — um tronco de outro nó, a ETE, a janela. E é exatamente isso que este
problema relaxado joga fora. No problema que ele resolve, ela custa zero e cabe
sempre; excluí-la puxava o número PARA BAIXO do alcançável, e um teto que fica
abaixo do que o otimizador consegue deixa de ser teto — descarta uma análise que
valia a pena, e o erro é invisível porque ninguém roda a simulação para descobrir
que o aviso estava errado.

Foi a versão anterior desta função, e o argumento dela ("dinheiro nenhum a
compra") confundia dois problemas: no problema REAL ela não é comprável, mas o
teto não é sobre o problema real — é sobre a relaxação, e na relaxação ela é.

Ela continua reportada à parte, em `subbaciasSemCapexProprio`, porque a leitura
"N delas não dependem de orçamento" é resposta, e não sobra: quem quiser essas
precisa mexer em precedência ou capacidade, não no orçamento.
"""

#: A INTERFACE PÚBLICA deste módulo. Tudo o que não está aqui é implementação,
#: e pode mudar sem aviso — a lista é o contrato com quem importa.
__all__ = [
    "DEGRAUS",
    "MINIMO_DE_PONTOS",
    "MAXIMO_DE_PONTOS",
    "MAIOR_DEGRAU",
    "FaixaInvalida",
    "pontos_da_faixa",
    "Candidata",
    "teto",
]

import math
from typing import Any

#: A faixa PADRAO da curva, em % A MAIS de CAPEX por ano — usada quando quem
#: pergunta nao escolhe outra.
#:
#: Nao e mais a unica: a faixa e os pontos sao de quem analisa, porque "quanto a
#: mais e plausivel" e pergunta de negocio e muda por unidade — uma concessao em
#: fim de ciclo discute +5% a +15%, e nao +10% a +50%.
#:
#: O que continua fixo e o LIMITE de pontos, e a razao e o custo: cada ponto e
#: uma execucao de solver, e granularidade fina nao seria mais informacao, seria
#: mais espera. Ver `pontos_da_faixa`.
DEGRAUS = (10, 20, 30, 40, 50)

#: Quantos pontos uma analise pode pedir de uma vez.
#:
#: UM e o minimo, e e o caso comum: a pessoa escolhe um acrescimo, roda, le o
#: resultado no grafico e decide se quer outro. A curva se forma ponto a ponto, e
#: cada um custa uma execucao de solver — pedir varios de uma vez e a excecao, nao
#: o padrao.
#:
#: Cinco continua sendo o teto pelo mesmo motivo de sempre: acima disso o custo
#: (cinco execucoes) deixa de se pagar em leitura.
MINIMO_DE_PONTOS = 1
MAXIMO_DE_PONTOS = 5

#: O maior degrau aceito. Acima disso a pergunta deixa de ser sensibilidade e
#: vira outro plano: o orcamento nao e um dial que a operacao gira, e uma curva
#: que sugere +500% convida a uma leitura que a realidade nao autoriza.
MAIOR_DEGRAU = 200


class FaixaInvalida(ValueError):
    """A faixa pedida nao descreve uma varredura — vira 422 com a mensagem."""


def pontos_da_faixa(inicio: int, fim: int, quantos: int) -> list[int]:
    """Os degraus de uma varredura: `quantos` pontos de `inicio` a `fim`.

    As duas pontas SEMPRE entram — sao elas que a pessoa escolheu, e os
    intermediarios existem para mostrar o que acontece no meio. Uma varredura que
    nao passasse pelos extremos responderia outra pergunta.

    ## Inteiros, e o descarte de repetidos

    O degrau e a IDENTIDADE do ponto na curva: e por ele que a tela casa a rodada
    que voltou com a coluna do grafico. Fracionario, essa identidade passaria a
    depender de dois arredondamentos concordarem — o do servidor ao ler o fator
    gravado, e o do cliente ao planejar o ponto —, e um centesimo de diferenca
    faria o ponto sumir do grafico com a rodada pronta no banco.

    O preco e faixa estreita render menos pontos que os pedidos: de 10 a 12 em
    cinco daria 10, 10.5, 11, 11.5, 12, e sobram tres inteiros. Devolver tres e
    o certo — rodar duas vezes o mesmo orcamento gastaria cluster para desenhar o
    mesmo ponto duas vezes. Quem chama diz na tela quantos vao rodar de verdade.
    """
    if quantos < MINIMO_DE_PONTOS or quantos > MAXIMO_DE_PONTOS:
        raise FaixaInvalida(
            f"A análise tem de ter entre {MINIMO_DE_PONTOS} e {MAXIMO_DE_PONTOS} pontos."
        )
    if inicio < 1 or fim < 1:
        raise FaixaInvalida("Os degraus são acréscimos de CAPEX: precisam ser maiores que zero.")
    # UM PONTO NAO TEM FIM: `inicio` e a resposta inteira, e exigir `fim > inicio`
    # recusaria justamente o pedido mais comum — "rode +25% e me mostre".
    if quantos == 1:
        if inicio > MAIOR_DEGRAU:
            raise FaixaInvalida(f"O maior acréscimo aceito é {MAIOR_DEGRAU}%.")
        return [inicio]
    if fim <= inicio:
        raise FaixaInvalida("O fim da faixa precisa ser maior que o início.")
    if fim > MAIOR_DEGRAU:
        raise FaixaInvalida(f"O maior acréscimo aceito é {MAIOR_DEGRAU}%.")

    # `floor(x + 0.5)`, E NAO `round`. Os dois so discordam no meio exato — e o
    # meio exato acontece: de 1 a 100 em cinco pontos cai em 50.5. Ali o `round`
    # do Python arredonda PARA O PAR (50) e o `Math.round` do JavaScript
    # arredonda PARA CIMA (51).
    #
    # Uma divergencia de um ponto entre servidor e tela nao e cosmetica: o degrau
    # e a IDENTIDADE do ponto na curva. O teto viria calculado para +50% e a tela
    # dispararia +51%, e o quadro de teto passaria a falar de um degrau que
    # ninguem vai rodar — com os dois numeros plausiveis e nada denunciando.
    #
    # Meio-para-cima e a regra dos dois lados, porque e a que o JavaScript nao
    # deixa escolher. Ver `pontosDaFaixa` no front, e o teste que compara as
    # duas nos mesmos casos.
    passo = (fim - inicio) / (quantos - 1)
    brutos = [math.floor(inicio + passo * i + 0.5) for i in range(quantos)]
    # `dict.fromkeys` em vez de `set`: preserva a ordem crescente que o `round`
    # já produziu, e a ordem é a da leitura da curva.
    return list(dict.fromkeys(brutos))


class Candidata:
    """Uma sub-bacia fora do plano, com o que custa e o que traz."""

    __slots__ = ("sub_bacia", "capex", "vazao")

    def __init__(self, sub_bacia: str, capex: float, vazao: float) -> None:
        self.sub_bacia = sub_bacia
        self.capex = capex
        self.vazao = vazao


def teto(
    candidatas: list[Candidata],
    orcamento_total: float,
    degraus: list[int],
    anos_do_plano: int = 0,
) -> dict[str, Any]:
    """O teto para cada degrau, sobre o mesmo conjunto de candidatas.

    `orcamento_total` é o da rodada base — a SOMA DOS ANOS, e não o valor anual —,
    e o degrau é em % A MAIS por ano (10 = +10%). Como a mesma porcentagem
    aplicada a cada ano equivale à mesma porcentagem sobre a soma, `folga` sai da
    conta direta; `anos_do_plano` viaja junto para a tela poder dizer sobre
    quantos anos aquele dinheiro está somado, que é o que desfaz a ambiguidade.
    """
    # AS DE GRAÇA ENTRAM SEMPRE, e sem consumir folga. Ver o docstring do módulo.
    de_graca = [c for c in candidatas if c.capex <= 0]
    pagaveis = [c for c in candidatas if c.capex > 0]
    piso_de_contagem = len(de_graca)
    piso_de_vazao = sum(c.vazao for c in de_graca)

    # Uma ordenação para cada pergunta. Ver o docstring do módulo.
    por_preco = sorted(pagaveis, key=lambda c: (c.capex, c.sub_bacia))
    por_eficiencia = sorted(pagaveis, key=lambda c: (-(c.vazao / c.capex), c.sub_bacia))

    linhas = []
    for degrau in degraus:
        folga = orcamento_total * degrau / 100.0

        gasto = 0.0
        cabem = piso_de_contagem
        for c in por_preco:
            if gasto + c.capex > folga:
                break
            gasto += c.capex
            cabem += 1

        restante = folga
        vazao = piso_de_vazao
        for c in por_eficiencia:
            if restante <= 0:
                break
            # O ÚLTIMO ENTRA FRACIONADO, de propósito. É o que faz disto um
            # limite superior da mochila inteira, e não mais uma solução viável:
            # a fração é dinheiro que o ótimo real não consegue converter em
            # vazão, e reconhecê-la mantém o número acima do alcançável.
            fracao = min(1.0, restante / c.capex)
            vazao += c.vazao * fracao
            restante -= c.capex * fracao

        linhas.append(
            {
                "degrau": degrau,
                "folga": round(folga, 2),
                "subbaciasNoMaximo": cabem,
                "vazaoNoMaximo": round(vazao, 2),
            }
        )

    return {
        "orcamentoTotal": round(orcamento_total, 2),
        "anosDoPlano": anos_do_plano,
        "subbaciasFora": len(candidatas),
        "subbaciasSemCapexProprio": len(de_graca),
        "capexParaTodas": round(sum(c.capex for c in pagaveis), 2),
        "vazaoTotalPresa": round(sum(c.vazao for c in candidatas), 2),
        "degraus": linhas,
    }
