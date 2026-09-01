# A categoria de uma sub-bacia que não fatura é a da sua obra de coleta

Um nó tem várias obras (ligação, rede, tronco, EEE) e elas discordam de
`categoria_motivo` com frequência — em `run_20260812_000112_0ba066`, 2148 dos
2269 nós que não faturam têm obras de categorias diferentes. A regra é ler a
categoria da obra nomeada em `otim_subbacia.obra_coleta`: é ela que coleta
daquele nó e decide se ele entra no plano.

## Considered Options

"A primeira obra do nó que tenha categoria" foi o que existiu até 29/08/2026, num
`SELECT` sem `ORDER BY` — resposta que muda entre duas execuções da mesma
consulta. O nível 1 chamava `c1b3_1_3` de "Nao se paga" enquanto o nível 4 a
chamava de "Compartilhada nao acionada", sobre a mesma sub-bacia e a mesma rodada.

## Consequences

A regra vale nos dois níveis, e é a mesma nos dois lugares (`explicabilidade` e
`subbacia`). Trocar um sem trocar o outro traz a divergência de volta.
