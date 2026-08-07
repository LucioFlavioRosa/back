# Ambiente de desenvolvimento

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
