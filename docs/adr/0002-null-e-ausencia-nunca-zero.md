# `null` significa "não existe", nunca 0

Em todo payload deste serviço, um campo nulo afirma que a conta não existe, e
nunca que ela deu zero. Divisão usa `NULLIF(divisor, 0)` e não
`COALESCE(..., 0)`: ocupação de uma ETE com capacidade zero é `null`, e a tela
mostra travessão — um `0%` ali afirmaria ETE vazia, que é uma informação
diferente e falsa.

A regra vale nos três estados também: meta fora da janela de CAPEX volta com
`atingida: null`, porque ninguém a avaliou. Enquanto isso foi `boolean`, a tela
dizia "não atingida" sobre meta que não estava em jogo — reportar falha
inexistente é pior que omitir.

## Consequences

Modelos de resposta (`app/api/formas_*.py`) declaram `| None` como afirmação, e
não por descuido. Trocar um `| None` por obrigatório é mudança de contrato.
