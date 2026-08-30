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

from typing import Any

#: Os degraus da curva, em % A MAIS de CAPEX por ano.
#:
#: Cinco, e nao uma escala continua, porque cada ponto pedido custa uma execucao
#: do solver — a granularidade fina nao seria mais informacao, seria mais espera.
#: Param aos 50% porque acima disso a pergunta deixa de ser sensibilidade e vira
#: outro plano: o orcamento nao e um dial que a operacao gira, e uma curva que
#: sugere +200% convida a uma leitura que a realidade nao autoriza.
DEGRAUS = (10, 20, 30, 40, 50)


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
