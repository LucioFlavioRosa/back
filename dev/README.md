# Ambiente de desenvolvimento

A pilha do `docker-compose` espelha os serviços Azure do desenho, em imagem local:

| Azure | Local |
|---|---|
| Database for PostgreSQL | `postgres:16-alpine` |
| Service Bus | emulador oficial (+ SQL Edge, que ele exige) |
| Blob Storage | Azurite |
| Cache for Redis | `redis:7-alpine` |
| Entra ID | **sem emulador** — e falsificar identidade em dev é como se aceita token forjado em produção |


O que existe aqui só serve para provar que o serviço fala com um Postgres de
verdade. Três dos erros mais caros deste repositório **só apareceram assim** — nome
de coluna errado, `jsonb` chegando como texto, e um handler de erro que estourava
em vez de responder. Nenhum teste sem banco pega esses.

```bash
docker compose up -d
# os DDLs vêm dos outros repositórios — ver a tabela de fontes no README raiz
docker compose exec -T db psql -U otim -d otimizador < .../ddl_input.sql
docker compose exec -T db psql -U otim -d otimizador < .../ddl_otimizador.sql
docker compose exec -T db psql -U otim -d otimizador < dev/seed.sql

python dev/smoke.py           # 21 GET + 1 POST: nenhum pode dar 5xx
python dev/formas.py          # os campos de cada resposta contra o CONTRATO.md
python dev/smoke_escrita.py   # as 6 escritas do cadastro, e a trilha junto
python dev/smoke_seguranca.py # os dez ataques que ja funcionaram

docker compose exec -T db psql -U otim -d otimizador < dev/seed_u2.sql
python dev/smoke_recorte.py   # com DUAS unidades: nada vaza de uma para a outra
python dev/smoke_pendencias.py # a conta que libera ou trava a simulacao
python dev/smoke_fila.py       # o disparo inteiro, com Service Bus de verdade
python dev/smoke_concorrencia.py # 10 POST simultaneos = 1 rodada
python dev/smoke_conflito.py   # versao por ficha (409) e a identidade da unidade
python dev/smoke_ida_e_volta.py # ler a ficha e salva-la de volta, sem traducao

# Ponta a ponta com o FRONT junto (nginx + FastAPI + Postgres + Service Bus):
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
# http://localhost:8080 e o produto inteiro, falando com a API de verdade
```

`seed.sql` é o **mínimo** que faz os 23 endpoints responderem: uma unidade, uma
cidade, um sistema, uma sub-bacia, uma obra, e uma rodada publicada. Não é dado
realista e não serve para conferir número — serve para exercitar caminho.

Duas respostas que **devem** falhar, e falham por estarem certas:

- `GET /runs/run_teste_1/status` dá 404: o seed popula só o lado do resultado,
  sem `controle.run_request`;
- `POST /runs` dá 503: não há Service Bus no ambiente local. E a rodada fica
  gravada como `ERRO`, que é a recuperação — se ficasse `PENDENTE`, o
  `/reexecutar` a recusaria para sempre por considerá-la em voo.

## Os smokes NAO sao isolados

Cada um espera o banco recem-semeado e altera dados. Rodar `smoke_seguranca` antes
de `smoke_conflito`, por exemplo, faz o segundo falhar por sujeira e nao por bug.
Entre um e outro, reaplique os DDLs e o seed. Esta declarado porque ja custou
diagnostico duas vezes.
