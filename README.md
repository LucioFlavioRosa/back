# Backend do Otimizador de CAPEX — Esgoto

API que liga as três pontas do produto:

```
front (React)  ──HTTP──▶  ESTE SERVIÇO  ──INSERT──▶  Postgres (input.*, controle.*)
                                        ──publica──▶  Service Bus
                                                          │
                                                          ▼
                                                    Job do Databricks
                                             (lê o cadastro, otimiza, publica)
                                                          │
front  ◀────────── lê public.otim_* ◀─────────────────────┘
```

O serviço **não** otimiza nada. Ele grava o cadastro, pede a execução e devolve o
resultado que o job publicou. O motor vive noutro repositório
(`LucioFlavioRosa/otimzador_capex`) e roda no Databricks.

## Onde está cada coisa

```
main.py              ponto de entrada (FastAPI + lifespan do pool e da fila)
app/
  config.py          tudo que vem do ambiente, lido uma vez
  api/               ── OS ENDPOINTS ──
    saude.py         /healthz e /readyz (fora do /api: quem chama é o kubelet)
    simulacao.py     disparar, acompanhar, reexecutar        CONTRATO.md §4
    cadastro.py      cadastro da unidade, leitura e escrita  DEPLOY.md §3
    resultados.py    os 11 endpoints de leitura             CONTRATO.md §3
    erros.py         todo erro sai como {"erro": "mensagem"} CONTRATO.md §1.1
    deps.py          usuário do token — quem assina a simulação
  dominio/           ── AS REGRAS, sem framework ──
    run_id.py        gramática do id e por que ele congela
    status.py        ciclo de vida da rodada e quem pode reexecutar
    parametros.py    corpo do front → params do run_request
  infra/             ── O MUNDO DE FORA ──
    db.py            pool asyncpg
    fila.py          Service Bus
    repositorios/    controle.py (run_request/status) · resultado.py (histórico
                     e meta) · niveis.py (a cascata) · cadastro.py (leitura) ·
                     cadastro_escrita.py (ficha + trilha) ·
                     pendencias.py (a mesma conta que a tela faz)
migracoes/           001_override.sql — a trilha de auditoria do cadastro
tests/               a superfície da API não pode derivar do contrato do front
```

Para entender o serviço, leia `app/dominio/` primeiro: são três arquivos sem
dependência de framework e é onde estão as decisões que custam caro se erradas.

## Rodar

```bash
python -m venv .venv && .venv/Scripts/activate    # Windows
pip install -r requirements.txt
cp .env.example .env                              # aponte para o seu Postgres
uvicorn main:app --reload
```

`GET /api/docs` traz o OpenAPI navegável.

Sem `ENTRA_TENANT_ID` o serviço **não exige token** e assume `dev@local` como
usuário — o mesmo arranjo do front, que só manda `Authorization` quando o SSO está
ligado. O `/readyz` denuncia esse modo (`"autenticacao": "DESLIGADA"`) para que ele
não chegue a produção sem alguém ver.

## As fontes de verdade

Este serviço não inventa contrato. Onde a dúvida se resolve:

| Pergunta | Onde está |
|---|---|
| Que endpoint o front chama, com que payload | `CONTRATO.md` do repo do front |
| Que `params` o job aceita | `job_databricks.MAPA_PARAMS` (repo do otimizador) |
| Como o job trata o `run_id`, retry e publicação | `docs/02-integracao-backend.md` |
| Que tabelas existem e o que cada coluna significa | `ddl_input.sql`, `ddl_resultado.sql`, `docs/06-dicionario-resultado.md` |
| Como se lê o resultado, nível a nível | `leitor_v2.py` + PARTE IV do notebook |

## O que ainda não está pronto

Declarado em vez de escondido — cada item tem o motivo e o caminho:

- **Faixas de paridade** (`cidade.paridade.faixas`): vêm de `input.fator_esgoto`,
  e o job publica só a paridade REALIZADA por ano, não a tabela de faixas que a
  produziu. A tela precisa das faixas para explicar a causalidade do degrau. Ou o
  job passa a publicá-las na rodada, ou este endpoint lê o cadastro — e aí o
  número deixa de ser o daquela rodada, o que é pior.
- **`_BASE_SUBBACIA`** em `cadastro_escrita.py` é cópia de `BASE_OBRAS` do front.
  O corpo manda só o que difere da base, por índice, então sem ela não há o que
  gravar — mas cópia envelhece: se a base mudar lá e não aqui, a ficha salva com
  valores de ontem sem nenhum sinal. O certo é o backend servir a base para a tela.
- **"Ficha inteira" é, na prática, merge**: campo ausente no corpo mantém o valor
  no banco em vez de limpá-lo. O contrato diz que o corpo é a ficha inteira e o
  front sempre a manda inteira, então na prática coincide — mas um cliente parcial
  vira um PATCH sem que nada acuse.
- **Validação do token do Entra ID** (`app/api/deps.py`): falta o JWKS do tenant.
  Está levantando erro em vez de decodificar sem verificar, de propósito.
- **Cancelar rodada**: bloqueado por migração (ver abaixo).
- **Nome da rodada**: perdido até a coluna `rotulo` existir (ver abaixo).
- **`progresso` do status** é sempre 0: `controle.run_status` não tem essa coluna.
  O modal do front nomeia a etapa a partir dele, então hoje ele salta de 0 a 100.
- **Nada disto foi executado contra um Postgres real.** As consultas foram escritas
  contra o DDL e conferidas coluna a coluna, mas isso é leitura, não teste.

## Migrações que este serviço precisa

Duas, no banco do pacote de produção:

1. **`CANCELADA` no CHECK de `controle.run_status`.** Hoje o CHECK aceita apenas
   `PENDENTE, RODANDO, SUCESSO, FALHOU_QUALIDADE, ERRO`, mas o `CONTRATO.md` §4.3 e
   a tela de simulação usam `CANCELADA`. Sem isso `POST /runs/{id}/cancelar` não
   pode existir sem mentir para o usuário.
2. **`rotulo` em `controle.run_request`.** O nome que o usuário dá à rodada não
   tem onde morar até a publicação. Ele viajava dentro do `params`, e a revisão
   mostrou o estrago: o job valida `params` contra `MAPA_PARAMS` + `CHAVES_DO_JOB`
   e `ROTULO` não está em nenhum dos dois — **toda rodada com nome morreria em
   `ERRO`**. Hoje o nome se perde no caminho; a alternativa era perder a rodada.
3. ~~**`input.override`**~~ — **feita** (`migracoes/001_override.sql`). Precisa ser
   aplicada nos bancos existentes e dobrada no `ddl_input.sql` do repositório do
   otimizador, que é quem é dono do esquema.
4. **`reprocessa_de` em `controle.run_request`.** Decidido junto da regra de
   imutabilidade do `run_id` (`CONTRATO.md` §2.1): a reexecução depois de um
   `SUCESSO` gera id novo, e sem esse campo o histórico vira uma lista de rodadas
   soltas, sem como ligar a rodada à sua origem.

## Uma divergência achada na leitura das fontes

O front oferece **`redistribuir_orcamento`** e **`teto_execucao_anual`**, e nenhum
dos dois existe no motor — conferi a assinatura de `ler_banco`. Eles são
pré-processamento da célula 3 do notebook: com a redistribuição ligada, cada ano
recebe o mesmo teto (o pico, ou o valor informado) e a soma da janela fica travada
em `ORCAMENTO_TOTAL`.

Essa tradução vive em `app/dominio/parametros.py`. Se ficasse no notebook, o job
receberia duas chaves desconhecidas — e ele recusa por contrato, com razão: a
rodada morreria em `ERRO` com uma mensagem sobre `params` sem relação visível com o
botão que o usuário apertou.
