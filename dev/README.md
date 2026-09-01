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

# Refaz as RODADAS sobre o cadastro atual: apaga resultados e roda as 5 unidades (~20 min).
python dev/recarregar_tudo.py

# Ponta a ponta com o FRONT junto (nginx + FastAPI + Postgres + Service Bus):
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
# http://localhost:8080 e o produto inteiro, falando com a API de verdade
```

## Os smokes

Todos descobrem unidade e ficha **pela própria API**. Nenhum fixa id: os que
fixavam quebraram no dia em que o dado real substituiu o seed, e a falha parecia
regressão do serviço quando era o teste olhando para dado que não está mais lá.

```bash
python testes_de_integracao/smoke.py             # 21 GET + 1 POST: nenhum pode dar 5xx
python testes_de_integracao/contrato_de_resultado.py            # respostas de RESULTADO vs CONTRATO.md
UNIDADE=uA1 python testes_de_integracao/contrato_de_cadastro.py  # respostas de CADASTRO vs os tipos do front
python testes_de_integracao/smoke_incons.py      # as CTS que existem pela metade
python testes_de_integracao/smoke_ida_e_volta.py # ler a ficha e salvá-la de volta, sem tradução
python dev/limpar_rodadas_de_teste.py # o que o histórico herdou de teste (só mostra)
```

`limpar_rodadas_de_teste.py` é o único destes que **escreve**, e só com `--apagar`
— sem a flag ele lista e sai. Ele existe porque um banco de desenvolvimento
acumula rodada de smoke, de seed sintético, execução morta em `ERRO` e a mesma
rodada disparada vinte vezes num laço de tela: o histórico deixa de ser legível e
a auditoria deixa de significar. **As regras dele não são lógica de aplicação e
nunca devem virar:** o serviço não decide sozinho que uma rodada é de teste —
rodada é imutável, e a única exclusão que o produto oferece é a que uma pessoa
pede, uma por vez.

`dev/legado_seed/` guarda os smokes presos ao seed sintético (`u1`, `b38_1`,
`c_rio`), que **não rodam** contra o dado real — ver o README de lá. Não estão
apagados porque cobrem o que nenhum outro cobre (segurança, fila, concorrência),
e não estão aqui porque falhariam sempre, ensinando todo mundo a ignorar falha
de smoke.

Uma resposta que **deve** falhar, e falha por estar certa:

- `POST /runs` dá 503: não há Service Bus no ambiente local. E a rodada fica
  gravada como `ERRO`, que é a recuperação — se ficasse `PENDENTE`, o
  `/reexecutar` a recusaria para sempre por considerá-la em voo.

(`GET /runs/{id}/status` **deixou** de dar 404: `dev/rodar_simulacao_real.py`
agora registra a rodada em `controle.run_request`/`run_status`, como o job faz em
produção. Antes ele publicava direto em `public.otim_*` e a tela de
acompanhamento não achava a rodada que a tela de resultados mostrava.)

## Os smokes NAO sao isolados

Cada um espera o banco recem-semeado e altera dados. Rodar `smoke_seguranca` antes
de `smoke_auditoria`, por exemplo, faz o segundo falhar por sujeira e nao por bug.
Entre um e outro, reaplique os DDLs e o seed. Esta declarado porque ja custou
diagnostico duas vezes.

## Dado de verdade, em vez de seed

`dev/legado_seed/seed.sql` é o mínimo para os endpoints responderem — não é dado
realista, e foi justamente por isso que três defeitos passaram (`otim_obra.sistema`
NULL, componente como código curto, obra de ETE sem `no`). Ele **saiu de `dev/`**
junto com os smokes que dependiam dele: os ids que ele cria (`u1`, `b38_1`,
`c_rio`) não existem no banco carregado da planilha, e rodá-lo por cima do dado
real só produz falha que parece do serviço.

Para navegar com uma simulação DE VERDADE:

```bash
python dev/rodar_simulacao_real.py uA1 90
```

Lê `input.*` do Postgres, roda o motor (OR-Tools) sobre a unidade e publica o
resultado em `public.otim_*`. **Não escreve no cadastro.** Precisa de `ortools` e
`psycopg2-binary` no host, e do pacote do motor em layout plano — por padrão
`projetos/pacote-motor-main`, ou o que `OTIMIZADOR_PACOTE` apontar.

Unidades: `uA1` (5 cidades, 142 sub-bacias — a mais rápida) até `uB2` (27/1116).
