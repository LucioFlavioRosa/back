"""A VARIAÇÃO DE ORÇAMENTO — os parâmetros de um ponto da curva de sensibilidade.

Uma variação é a MESMA simulação com o orçamento de cada ano multiplicado, e
nada mais mexido. É o que faz a comparação medir o efeito do orçamento em vez da
diferença entre duas simulações quaisquer.

## Os dois modos, e por que a diferença mora aqui

A rodada que a pessoa manda rodar é uma simulação normal: `montar_params` fixa
`MAX_TIME_S = 1000`, e é assim que ela decide um plano.

A ANÁLISE dela é outra coisa. São cinco variações de +10% a +50% cujo trabalho é
mostrar a INCLINAÇÃO da curva, e a inclinação aparece muito antes da prova de
otimalidade. Por isso o modo `rapido` põe `MAX_TIME_S = 60`: mesma otimização,
mesmos dados, mesmas restrições, menos relógio.

`MAX_TIME_S` entra nos `params`, e não numa coluna à parte — de propósito. O
digest da deduplicação é sobre `params`, então a estimativa de 60s e a simulação
de 1000s da mesma variação são pedidos DIFERENTES e não se deduplicam uma na
outra. Devolver a estimativa para quem pediu a simulação seria trocar a resposta
por outra sem avisar.

## O arredondamento a centavos não é cosmético

`60000000 * 1.1` dá `66000000.00000001`. O resíduo entraria no digest, e duas
formas de chegar ao mesmo orçamento deixariam de deduplicar entre si — a mesma
análise pedida duas vezes gastaria cluster duas vezes.
"""

from typing import Any, Literal

from app.dominio.parametros import ParametrosInvalidos

#: O teto de solver da ESTIMATIVA, em segundos, contra os 1000s de uma simulação
#: (ver `dominio/parametros.py`). Não é um número afinado: é a ordem de grandeza
#: que separa "responde enquanto a pessoa olha a tela" de "responde depois que
#: ela foi fazer outra coisa". O executor local ainda o limita pelo `--tempo`
#: dele, então 60 é um teto, não uma promessa de gastar 60.
SEGUNDOS_DA_ESTIMATIVA = 60

Modo = Literal["rapido", "completo"]

MODOS: tuple[str, ...] = ("rapido", "completo")


def params_da_variacao(
    base: dict[str, Any],
    *,
    unidade_id: str,
    usuario: str,
    fator: float,
    modo: Modo,
) -> dict[str, Any]:
    """Os parâmetros da variação, a partir dos da rodada de origem.

    `base` vem de `controle.parametros`, que já retira as chaves de execução
    (`USUARIO`, `MAX_TIME_S`, `WORKERS`) — então a rodada nova nasce assinada por
    quem a pediu, e o teto de solver é decidido AQUI, pelo modo, em vez de
    herdado sem querer da rodada de origem.

    Levanta `ParametrosInvalidos` quando o pedido não faz sentido, que a API já
    traduz em 422 com a mensagem no corpo. As recusas são a mesma ideia: sem
    fator positivo, sem modo conhecido ou sem orçamento gravado não existe "o
    mesmo plano com mais dinheiro".
    """
    if not isinstance(fator, (int, float)) or isinstance(fator, bool) or fator <= 0:
        raise ParametrosInvalidos(
            "O fator do orçamento precisa ser um número maior que zero — 1.1 é +10%."
        )
    if modo not in MODOS:
        raise ParametrosInvalidos('O modo precisa ser "rapido" ou "completo".')

    params = {**base, "UNIDADE": unidade_id, "USUARIO": usuario}
    if modo == "rapido":
        params["MAX_TIME_S"] = SEGUNDOS_DA_ESTIMATIVA

    orcamento = params.get("ORCAMENTO")
    if isinstance(orcamento, dict):
        params["ORCAMENTO"] = {ano: round(float(v) * fator, 2) for ano, v in orcamento.items()}
    elif isinstance(orcamento, (int, float)) and not isinstance(orcamento, bool):
        # Orçamento anual único: o motor recebe o número e o horizonte, e
        # distribui. Escalar o escalar é a mesma operação.
        params["ORCAMENTO"] = round(float(orcamento) * fator, 2)
    else:
        raise ParametrosInvalidos(
            "A rodada de origem não tem orçamento gravado — não há o que escalar."
        )

    # `ORCAMENTO_TOTAL` é a soma travada quando houve redistribuição entre anos:
    # deixá-la como estava faria o teto novo brigar com um total antigo, e o
    # motor recusaria o plano que ele mesmo acabou de receber.
    if "ORCAMENTO_TOTAL" in params:
        params["ORCAMENTO_TOTAL"] = round(float(params["ORCAMENTO_TOTAL"]) * fator, 2)

    return params
