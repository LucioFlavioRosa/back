# Prefixo e guarda vivem no roteador, e um teste cobra o conjunto

Cada `APIRouter` declara o próprio `prefix="/api"` e
`dependencies=[Depends(guarda_de_rota)]`, em vez de recebê-los no
`include_router`. Quem abre `app/api/resultados.py` vê sob que caminho aquelas
rotas respondem e que elas nascem protegidas, sem ter de ir ao `main.py`.

## Consequences

Perdeu-se a visão de conjunto que as três linhas juntas davam — nelas, um
roteador sem guarda saltava do diff. `tests/test_guarda_de_rota.py` a devolve
como invariante: ele varre as rotas MONTADAS e cobra a dependência em toda
`APIRoute` sob `/api`. Roteador novo sem guarda quebra o build, que é mais forte
que o olho.
