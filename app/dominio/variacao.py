"""A VARIAÇÃO DE ORÇAMENTO — os parâmetros de um ponto da curva de sensibilidade.

Uma variação é a MESMA simulação com o orçamento de cada ano multiplicado, e
nada mais mexido. É o que faz a comparação medir o efeito do orçamento em vez da
diferença entre duas simulações quaisquer.

## Os dois modos, e por que a diferença mora aqui

A rodada que a pessoa manda rodar é uma simulação normal: `montar_params` fixa
`MAX_TIME_S = 1000`, e é assim que ela decide um plano.

A ANÁLISE dela é outra coisa. São cinco variações de +10% a +50% cujo trabalho é
mostrar a INCLINAÇÃO da curva, e a inclinação aparece muito antes da prova de
otimalidade. Por isso o modo `rapido` corta o tempo de solver: mesma otimização,
mesmos dados, mesmas restrições, menos relógio.

QUANTO MENOS DEPENDE DO TAMANHO DO MODELO: um valor fixo para todas as unidades
sobra na pequena e falta na grande. Ver `segundos_da_estimativa`.

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

#: A INTERFACE PÚBLICA deste módulo. Tudo o que não está aqui é implementação,
#: e pode mudar sem aviso — a lista é o contrato com quem importa.
__all__ = [
    "SEGUNDOS_MINIMOS_DA_ESTIMATIVA",
    "SEGUNDOS_MAXIMOS_DA_ESTIMATIVA",
    "COLUNAS_POR_SEGUNDO",
    "segundos_da_estimativa",
    "Modo",
    "MODOS",
    "params_da_variacao",
]

from typing import Any, Literal

from app.dominio.parametros import ParametrosInvalidos

#: O piso do teto de solver da estimativa, em segundos.
#:
#: Abaixo disto não vale chamar de execução: o motor reparte `max_time_s * 1.35`
#: entre três fases e pula a última quando sobram menos de 5s.
SEGUNDOS_MINIMOS_DA_ESTIMATIVA = 60

#: O TETO DA ESTIMATIVA, em segundos. Uma simulação normal usa 1000s
#: (`montar_params`); a estimativa para em 500 por duas razões medidas na uA3, a
#: maior unidade:
#:
#:   - 500s BASTA. Rodou ÓTIMO, obrig 126/126, em 11m57s no total.
#:   - abaixo disso não fica mais rápido. Medido: com 180s a MESMA rodada levou
#:     16m14s — quatro minutos A MAIS. `MAX_TIME_S` não é o relógio da rodada, é
#:     um teto POR SOLVE, aplicado em frações (0.35×, 0.4×, 0.6×, 1.0×) a vários
#:     solvers dentro de `resolver_por_sistema`, mais 5s por sistema na geração
#:     de colunas. Cada um para antes do teto quando prova o ótimo, então o total
#:     é a soma de muitos solves: cortar o teto de cada um muda o caminho de
#:     busca, e não encurta a soma.
#:
#: Acima de 500 a estimativa deixaria de se distinguir da simulação sem ganhar
#: nada — e o resto do tempo da uA3 (3m23s) é materialização, que nenhum ajuste
#: de solver toca.
SEGUNDOS_MAXIMOS_DA_ESTIMATIVA = 500

#: COLUNAS DE MODELO POR SEGUNDO DE SOLVER. É a constante que transforma o
#: tamanho do problema em tempo, e cada dígito dela veio de medição:
#:
#:   unidade  obras × anos   teto    desfecho
#:   uB2        4.037 × 3 =  12.111   60s   ok (5 estimativas seguidas)
#:   uA2        2.518 × 10 = 25.180   60s   ok
#:   uA3        8.079 × 9 =  72.711   60s   FALHOU DUAS VEZES
#:   uA3                              500s  ok — 11m57s no total (medido 30/08)
#:   uA3                              180s  ok — 16m14s, QUATRO MINUTOS A MAIS
#:
#: 60s FIXO ERA O DEFEITO. Dar o mesmo orçamento de relógio a um modelo seis
#: vezes maior não é "rápido", é insuficiente — e insuficiente no motor não
#: devolve um plano pior, MATA a rodada: uma vez com `KeyError` no reparo do teto
#: anual (a seleção sai incompleta), outra com falha nativa do processo. As duas
#: aconteceram na uA3 a 60s, nesta ordem, no mesmo dia.
#:
#: 145 é o que faz a maior unidade medida receber os ~500s que se SABE que
#: bastam. É calibração sobre a ponta difícil, não uma lei — e é justamente por
#: isso que a tela mantém a saída de escalar para a simulação completa quando
#: mesmo assim falhar.
COLUNAS_POR_SEGUNDO = 145


def segundos_da_estimativa(colunas: int | None) -> int:
    """Quanto tempo de solver a estimativa deste modelo recebe.

    `colunas` é `obras_total × anos_capex` da rodada base — a medida mais direta
    do tamanho do MILP que o front já tem no cabeçalho da rodada. `None` (rodada
    que não publicou esses números) cai no piso, que é o comportamento anterior.
    """
    if not colunas or colunas <= 0:
        return SEGUNDOS_MINIMOS_DA_ESTIMATIVA
    proporcional = round(colunas / COLUNAS_POR_SEGUNDO)
    return max(
        SEGUNDOS_MINIMOS_DA_ESTIMATIVA, min(SEGUNDOS_MAXIMOS_DA_ESTIMATIVA, proporcional)
    )

Modo = Literal["rapido", "completo"]

MODOS: tuple[str, ...] = ("rapido", "completo")


def params_da_variacao(
    base: dict[str, Any],
    *,
    unidade_id: str,
    usuario: str,
    fator: float,
    modo: Modo,
    colunas: int | None = None,
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
        params["MAX_TIME_S"] = segundos_da_estimativa(colunas)

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
