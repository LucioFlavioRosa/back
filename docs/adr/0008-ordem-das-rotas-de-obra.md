# `/obras/cronograma` é declarada antes de `/obras/{obra_id}`

O FastAPI casa rota por ordem de declaração. Com a parametrizada primeiro,
`GET /runs/{id}/obras/cronograma` responde 404 "Obra não encontrada" — a rota
existe, e o servidor a trata como um id de obra chamado "cronograma". A ordem em
`app/api/resultados.py` é contrato, não estilo.

## Consequences

Reordenar as rotas por afinidade ou alfabeticamente quebra o endpoint sem quebrar
teste de import. Qualquer rota literal irmã de uma parametrizada tem a mesma
regra.
