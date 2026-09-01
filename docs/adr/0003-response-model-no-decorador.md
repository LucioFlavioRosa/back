# O modelo de resposta entra pelo decorador, não pela anotação de retorno

As 35 rotas declaram `response_model=` e continuam devolvendo o dicionário cru
que o repositório monta a partir do asyncpg. O caminho óbvio — anotar o retorno
da função com o modelo — obrigaria cada repositório a construir objetos Pydantic,
espalhando a forma do payload por duas camadas que envelheceriam em ritmos
diferentes.

## Consequences

`response_model` FILTRA campo não declarado, em silêncio. Modelo incompleto não
dá erro: some com o dado. Por isso `tests/test_formas_nao_filtram.py` constrói os
ramos que dependem de estado raro — foi assim que o bloco `fila` de
`/runs/{id}/status`, que só existe em rodada PENDENTE ou RODANDO, voltou depois
de ter sido comido por um modelo incompleto.
