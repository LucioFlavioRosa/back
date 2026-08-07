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
    resultados.py    histórico e leitura de uma rodada       CONTRATO.md §3
    erros.py         todo erro sai como {"erro": "mensagem"} CONTRATO.md §1.1
    deps.py          usuário do token — quem assina a simulação
  dominio/           ── AS REGRAS, sem framework ──
    run_id.py        gramática do id e por que ele congela
    status.py        ciclo de vida da rodada e quem pode reexecutar
    parametros.py    corpo do front → params do run_request
  infra/             ── O MUNDO DE FORA ──
    db.py            pool asyncpg
    fila.py          Service Bus
    repositorios/    SQL, uma classe de assunto por arquivo
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

- **Cadastro (`input.*`)**: nenhum endpoint ainda. São 16 tabelas e a escrita é por
  ficha, com trilha de override; o contrato está em `DEPLOY.md` §3 do front.
- **Sete endpoints de resultado**: `painel`, `ebitda`, `cidades`, `cidade`,
  `topologia`, `subbacia` e `obra`. As consultas saem das mesmas
  `public.otim_*`, e a lógica de agregação de cada um já existe em pandas no
  `leitor_v2.py` — é de lá que devem sair, não de uma releitura do esquema.
- **Validação do token do Entra ID** (`app/api/deps.py`): falta o JWKS do tenant.
  Está levantando erro em vez de decodificar sem verificar, de propósito.
- **`pendencias_do_cadastro`** devolve 0, o que hoje deixa qualquer rodada passar.
  É a mesma conta que o front faz em `cadastro/domain`, e precisa virar SQL.
- **Cancelar rodada**: bloqueado por migração (ver abaixo).

## Migrações que este serviço precisa

Duas, no banco do pacote de produção:

1. **`CANCELADA` no CHECK de `controle.run_status`.** Hoje o CHECK aceita apenas
   `PENDENTE, RODANDO, SUCESSO, FALHOU_QUALIDADE, ERRO`, mas o `CONTRATO.md` §4.3 e
   a tela de simulação usam `CANCELADA`. Sem isso `POST /runs/{id}/cancelar` não
   pode existir sem mentir para o usuário.
2. **`reprocessa_de` em `controle.run_request`.** Decidido junto da regra de
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
