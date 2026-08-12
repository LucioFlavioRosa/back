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
                     007_trilha_do_cadastro.sql — ela passa a cobrir a ficha
                     inteira, e quem calcula o diff é o servidor
                     005_capex_derivado.sql — o banco recusa CAPEX que não seja
                     quantidade × preco_unitario
                     006_auditoria_cadastro.sql — última alteração e autor por
                     ficha
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

Sem `ENTRA_AUDIENCE` o serviço **não exige token** e assume `dev@local` como
usuário — o mesmo arranjo do front, que só manda `Authorization` quando o SSO está
ligado. O `/readyz` denuncia esse modo (`"autenticacao": "DESLIGADA"`) para que ele
não chegue a produção sem alguém ver.

## A autenticação

A verificação do token é **OIDC padrão** e vive em `app/infra/tokens.py`: baixa o
JWKS do emissor, acha a chave pelo `kid`, confere a assinatura RS256 e então
`aud`, `iss` e `exp`. O Entra é um emissor como outro qualquer nessa conta, e por
isso os endereços vêm de configuração.

O **login** sai do token; o **escopo** (quais unidades) não. Ele continua em
`controle.usuario_acesso`, porque que unidades alguém acessa é decisão do negócio,
não do diretório corporativo. Login sem concessão autentica e não vê nada — falha
fechada, de propósito.

### Testar SSO local, sem tenant

`docker-compose.sso.yml` sobe um provedor OIDC de mentira
(`navikt/mock-oauth2-server`) e liga a autenticação:

```
docker compose -f docker-compose.yml -f docker-compose.e2e.yml -f docker-compose.sso.yml up -d
python dev/token_de_teste.py ana                      # imprime um Bearer válido
python dev/token_de_teste.py ana --ver                # mostra os claims
curl -H "Authorization: Bearer $(python dev/token_de_teste.py ana)" localhost:8000/api/regionais
```

Três usuários, com escopos diferentes de propósito — é o que faz o recorte
aparecer: `dev` (admin, tudo), `ana` (regional `rA`), `bruno` (unidade `uB2`).

Isso exercita a verificação e o recorte. **Não** exercita nada específico do
Entra: token v1 vs v2, `aud` como GUID ou `api://…`, qual claim carrega o login,
conditional access, consent. Para essas só serve um tenant de verdade — e
`ENTRA_CLAIM_LOGIN` existe justamente para a última delas não virar mudança de
código.

### O que muda ao apontar para o Entra

Só endereço. `ENTRA_TENANT_ID` e `ENTRA_AUDIENCE` preenchidos, e
`ENTRA_JWKS_URL`/`ENTRA_ISSUER` vazios — aí eles são derivados do tenant
(`.../discovery/v2.0/keys` e `.../v2.0`). Nenhuma linha de `app/` muda.

## As regras do produto

Seis regras do dono do produto. Tudo neste serviço existe para servi-las, e uma
mudança que não serve a nenhuma provavelmente não deveria entrar.

| | regra |
|---|---|
| **R1** | O banco SQL é a **verdade absoluta**. Nenhum valor nasce de constante no código. |
| **R2** | **Não inventar nem inserir nada.** Nenhuma gravação de valor que o usuário não digitou e que não veio do banco. |
| **R3** | Dado faltando: **avisar e travar a simulação da unidade inteira**. Exceção única: `wacc` ausente usa o WACC da unidade. |
| **R4** | Só **cadastro** é editável. Simulação, uma vez escrita, só pode ser **excluída**. |
| **R5** | Dedupe de simulação por pedido + **usuário**. Mesmo usuário, pedido idêntico → aponta para a simulação existente. Vale para rodada em voo e concluída; `ERRO` libera nova execução. |
| **R6** | Mostrar **última atualização e autor** no cadastro. |

## Como comentar e documentar

Comentário descreve **o que o código faz hoje**, o contrato do dado, ou o motivo
de uma decisão não óbvia. Não narra o passado.

- Motivo que previne regressão vira **regra no presente**: "o autor vem do token,
  nunca do corpo — autoria que o cliente escolhe não é auditoria".
- "Antes era", "saiu", "virou", "na revisão", "eu tinha errado", "medido no dia":
  **corte**. Isso é processo, e ele já está na mensagem de commit e no `git blame`.
- Docstring diz o que a função **garante**. Relato de incidente fica fora do código.
- Teste documenta o **comportamento protegido**, não como o defeito foi descoberto.

Duas regras que valem o dobro por já terem custado caro:

- **Comentário que mente conta como defeito.** Esta base explica muito em
  comentário e as pessoas confiam nele. `pendencias.py` já disse *"wacc NUNCA
  conta"* enquanto a lista abaixo cobrava.
- **Fixture que não espelha o payload real testa um produto que não existe.** Ao
  mudar a forma de uma resposta, mude a fixture no mesmo commit.

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
- **SSO no FRONT**: o backend valida o token (ver abaixo), mas nenhuma tela faz
  login ainda. O encaixe existe (`src/comum/auth/sessao.ts`) e espera uma chamada
  de `configurarSessao({ token })` com o MSAL. Depende da app registration.
- **Cancelar rodada**: bloqueado por migração (ver abaixo).
- **`reprocessa_de`**: falta a coluna para ligar a rodada reexecutada à origem
  (ver "Migrações", abaixo).
- **`progresso` do status**: a coluna existe (`migracoes/002_progresso.sql`) e o
  endpoint a serve, mas quem tem de escrevê-la é o JOB. `dev/worker.py` escreve,
  nas mesmas faixas que o front usa para nomear a etapa.
> **Tudo aqui roda contra um Postgres de verdade.** O `docker-compose` sobe um
> Postgres 16, `dev/recarregar_tudo.py` carrega as 5 unidades da planilha e roda as
> 5 simulações, e os smokes de `dev/` batem nos endpoints contra esse banco. Vale
> o esforço: três classes de erro só aparecem assim — nome de coluna errado,
> `jsonb` chegando como texto, e handler de erro que estoura em vez de responder.
> Nenhuma delas é alcançável por teste com banco de mentira.

## Uma decisão de modelagem: a CTS não se cria pela tela

**Não existe `POST /cts` nem `DELETE /cts`**, e o `DEPLOY.md` §3 do front diz o
mesmo.

A CTS é um **nó do sistema**, como a sub-bacia: a posição dela já está em
`input.sistema_topologia`, com jusante próprio — no banco carregado da planilha,
todas as 337 estão lá. O motor
monta os nós percorrendo a topologia, e faz `cts_ids = fichas ∩ nós` — só é CTS
efetiva a ficha que **também** é nó.

Então os dois endpoints estavam conceitualmente errados:

- `POST` gravava ficha + par sem tocar na topologia: criava uma CTS visível no
  cadastro e **invisível para a simulação**;
- `DELETE` era pior — apagava a ficha e deixava o nó, virando um nó de demanda
  **zero**; e como o par também sumia, com `USAR_CTS=false` a demanda dela deixava
  de ser somada à sub-bacia irmã. Destruía dado de duas formas ao mesmo tempo.

`subbacia_cts` é **sobreposição de área**, não pertencimento: é ela que permite ao
`USAR_CTS` escolher entre tratar a CTS como estrutura própria ou somar ligações,
receita e vazão dela na sub-bacia pareada.

Sobra o que faz sentido: `GET /cts` e `PUT /cts/{id}` — ler e editar a ficha de uma
CTS que o cadastro já tem. Criar ou remover CTS é mudança de topologia, e topologia
vem do cadastro estrutural.

### A CTS que existe pela metade

A CTS precisa de **três** coisas: nó em `sistema_topologia` (a posição na rede,
com jusante próprio), ficha em `cts_operacional` (a demanda) e par em
`subbacia_cts` (a sobreposição de área). O motor faz `cts_ids = fichas ∩ nós`, e o
par é o que permite ao `USAR_CTS` somar a demanda na sub-bacia irmã quando ela é
desligada.

Faltando qualquer uma, o efeito é **silencioso**: a rodada roda, o plano sai, e o
número está errado sem nenhum erro em lugar nenhum. O pior caso é o nó sem ficha —
ele *entra* na simulação, com demanda zero, ocupando posição na rede.

Por isso `GET /cts` devolve `inconsistencias: [{ tipo, id, subId, detalhe }]`, com
`tipo` em `ficha-sem-no` / `no-sem-ficha` / `sem-par`, e a tela as lista. Elas
**cruzam** com `ctss` em vez de substituí-lo: uma CTS com ficha mas sem nó aparece
nos dois — continua editável, e se sabe que a simulação não a vê — enquanto
um nó sem ficha só existe na denúncia, porque não há ficha para editar.

No banco há 2 casos (`cts_b2b80_1_3` em uA2, `cts_c2b12_3_1` em uA3), ambos
`ficha-sem-no` e sem componente nenhum. **Eles não vieram da planilha**: ela tem
337 CTS e o banco tem 339, e a diferença é exatamente esses dois — foram criados
pelo antigo `POST /cts`, que gravava ficha e par sem tocar na topologia. É
resíduo de teste, não defeito do cadastro de origem. Ver `dev/conferir_planilha.py`.

Isso não diminui a denúncia: ela é o que tornou os dois visíveis, e o mesmo
estado pode ser produzido por qualquer carga parcial. `dev/smoke_incons.py` cobre isso perguntando ao banco quais
deveriam aparecer e conferindo que a API disse exatamente aquilo — sem fixar ids,
para não falhar no dia em que o cadastro for corrigido.

## A trilha de auditoria do cadastro

Quem mudou o quê, quando — e **quem calcula é este serviço**, não o cliente.

`input.override` é a tabela, append-only, e cobre a ficha INTEIRA: bloco `db`,
bloco `params`, obras, cidade, metas, faixas de paridade e ETE. A coluna `origem`
(`migracoes/007`) diz de onde o campo vem — `databricks` ou `regional`.

**Quem calcula a diferença é `cadastro_escrita.diferencas`**, comparando o que
está gravado com o que chegou, campo a campo, **antes** de cada gravação. O
"antes" é obrigatório: obras e metas gravam por `DELETE`+`INSERT` em bloco, e
depois não sobraria com o que comparar. O corpo do `PUT` não carrega trilha
nenhuma — cliente não é fonte confiável sobre o que ele mesmo mudou. O `autor` vem
do token.

Três propriedades que valem saber:

- **Salvar sem mudar nada não grava linha.** A comparação é contra o dado, e não
  contra o último registro da trilha, então não há dedupe a fazer.
  `alteracoesGravadas: 0` é resposta legítima.
- **`GET /unidades/{id}/alteracoes`** serve a trilha à tela (teto de 200 linhas,
  com `cortado: true` quando bate no teto). Auditoria que só o DBA alcança não é
  auditoria do produto.
- **O volume é do uso, não da carga.** Só o que passa pelo `PUT` vira linha;
  recarregar a planilha não gera uma sequer.

## O contrato do EXECUTOR

Quem executa a rodada é outro processo: hoje `dev/worker.py`, em produção o job do
Databricks. **O backend não sabe qual dos dois é, e não deve saber.** Ele grava no
banco, publica na fila, e para por aí.

Trocar um pelo outro é **configuração**: `SERVICE_BUS_CONN` e `FILA_SIMULACOES`
(`app/config.py`). Nenhuma linha de `app/` muda.

O que não é configuração é o **contrato de banco** abaixo. Ele está escrito aqui,
e não só implícito no `dev/worker.py`, para que o job de produção possa ser
implementado sem ler o código da imitação.

### O que o backend faz, e nesta ordem

```
1. INSERT controle.run_request  (run_id, unidade, params, solicitado_por, rotulo)
2. INSERT controle.run_status   (run_id, 'PENDENTE')          — mesma transação
3. publica na fila              {"run_id", "unidade_id", "solicitado_por"}
```

A ordem não é negociável: gravar depois de enfileirar deixaria o job acordar e não
encontrar a `run_request`. Se o passo 3 falhar, o backend marca a rodada `ERRO`
com a causa — nunca a deixa `PENDENTE`, porque `PENDENTE` é lido como "em voo" e o
`/reexecutar` a recusaria para sempre.

### A mensagem carrega o mínimo, DE PROPÓSITO

```jsonc
{ "run_id": "run_...", "unidade_id": "uA1", "solicitado_por": "ana@aegea" }
```

Os parâmetros **não** viajam nela. A fonte de verdade é `controle.run_request`, e
uma cópia na mensagem envelheceria em relação ao banco. O executor lê por `run_id`.

`message_id` = `run_id` no primeiro envio (protege contra o retry de rede do SDK
virar duas execuções) e chave própria no reenvio pedido por gente. Duas execuções
do mesmo `run_id` são seguras — a publicação é idempotente —, ao passo que uma
rodada que nunca executa não é.

### O que o executor DEVE fazer

| # | passo | onde | se não fizer |
| --- | --- | --- | --- |
| 1 | ler `run_request` por `run_id` | `params`, `unidade`, **`rotulo`**, **`solicitado_por`** | roda com parâmetro errado, ou não roda |
| 2 | marcar `RODANDO` | `controle.run_status.status` | a tela mostra "na fila" durante a execução inteira |
| 3 | atualizar `progresso` (0–100) | `controle.run_status.progresso` | a barra salta de 0 a 100 e o modal promete um acompanhamento que não existe |
| 4 | publicar o resultado | `public.otim_*`, **transacionalmente** | rodada meio publicada; a tela lê tabela incompleta |
| 5 | `otim_meta.rotulo` e `.usuario` | **das colunas de `run_request`** | ver o aviso abaixo |
| 6 | marcar `SUCESSO` — ou `ERRO` com a causa | `controle.run_status` | fica `RODANDO` para sempre |

> **`rotulo` e `usuario` vêm das COLUNAS de `run_request`, nunca de `params`.**
>
> `ROTULO` foi tirado de `params` de propósito: o job valida `params` contra
> `MAPA_PARAMS` + `CHAVES_DO_JOB` e uma chave desconhecida mata a rodada
> (`migracoes/004_run_request_rotulo.sql`).
>
> O `dev/worker.py` lia `params["ROTULO"]`, que nunca existe, e caía num
> *fallback* `"{unidade} — pela tela"`. Efeito medido no banco local: alguém
> digitou **"Cenario com nome"** e o histórico publicou **"uA3 — pela tela"**.
> Três das seis rodadas nomeadas tiveram o nome substituído por um texto plausível
> que ninguém escreveu — no histórico, que existe justamente para distinguir uma
> rodada da outra. Consertado; fica aqui como o erro a não repetir.

### O vocabulário de `status`

`PENDENTE` · `RODANDO` · `SUCESSO` · `FALHOU_QUALIDADE` · `ERRO` · `CANCELADA`
(`app/dominio/status.py`).

`FALHOU_QUALIDADE` **não** é falha técnica: a rodada foi calculada e reprovou no
portão — o texto de `erro` é o que explica a diferença ao usuário.

Quem escreve o quê: o backend só cria `PENDENTE`. **As demais transições são do
executor**, com duas exceções declaradas, ambas sobre trabalho que parou de
acontecer: `ERRO` quando a fila falha ou quando o vigia encontra um lease vencido,
e `CANCELADA` quando alguém pede pelo `POST /runs/{id}/cancelar`. Nas duas o
executor obedece ao que encontrar — ele confere o status nos pontos em que a
rodada respira e larga o trabalho sem publicar.

### O que o backend NUNCA faz

Não escreve em `public.otim_*`. A única exceção é o `DELETE /runs/{id}`, que
exclui uma rodada inteira a pedido de uma pessoa (`resultado.excluir`) — R4: a
simulação, uma vez escrita, só pode ser excluída.

Isso é o que torna a troca de executor barata, e é a parte da tese que se sustenta
inteira: o resultado é de quem executa, do começo ao fim.

### Onde o backend PASSOU a depender do executor

Duas dependências novas, e vale saberem-se explícitas:

- **`public.otim_meta` como prova de publicação.** A dedupe de rodada concluída
  (R5) só reaproveita uma rodada `SUCESSO` que exista em `otim_meta`
  (`controle.rodada_identica`). Um executor que marque `SUCESSO` sem publicar
  deixa a rodada fora da dedupe — o que é o comportamento certo, mas é uma
  dependência que não existia antes.
- **`controle.run_diagnostico`** é lida e excluída pelo backend, e **nada no
  código disponível a popula**. Se o job de produção for escrever nela, o formato
  precisa ser combinado; se não for, a tabela é peso morto e merece sair.

## O que este serviço precisa do JOB

Nada aqui bloqueia, mas cada item é um remendo que some quando o job entregar:

1. **`unidade_id` em `otim_meta`.** Hoje a coluna `regional` guarda o *nome* da
   unidade, e o backend resolve o id fazendo join com `input.unidade_regional` pelo
   nome. Funciona, e tem duas fragilidades: depende de nome de unidade ser único, e
   renomear uma unidade desliga as rodadas antigas dela. Se a rodada é imutável
   (§2.1 do `CONTRATO.md`), a identidade dela deveria ser congelada junto — não
   reconstruída por nome a cada leitura.
2. **`cidade_id` e `sistema_id`** em `otim_cidade`/`otim_sistema`, ao lado dos
   nomes. Hoje o nome é usado como id, então o deep link vira
   `/cidades/Rio%20Bonito` e renomear a cidade quebra link salvo.
3. **`progresso`** em `controle.run_status` — sem ele o modal salta de 0 a 100.
4. **Faixas de paridade na rodada**: o job publica a paridade realizada, não a
   tabela cobertura→fator que a produziu, e a tela precisa dela para explicar o
   degrau.

## Migrações

`migracoes/` tem sete arquivos numerados. `app/infra/db.py` exige as que o serviço
não roda sem (`_EXIGIDO`/`_EXIGIDO_RESTRICAO`), e a falta de qualquer uma reprova o
`/readyz` — de propósito: pod que sobe sem elas serve dado errado em silêncio. O
índice do que cada uma cria está no `CHANGELOG.md`.

**Duas condições valem para toda migração do esquema `input`** (001, 005, 006,
007): aplicar nos bancos existentes, e dobrar no `ddl_input.sql` do repositório do
otimizador, que é o dono do esquema.

Nenhuma delas quebra a carga da planilha — o carregador só manda as colunas que
existem nos dois lados. A ponta a vigiar é a **005**: `carregar_postgres.py` manda
a coluna `capex` da planilha, e o CHECK a aceita porque o arredondamento da origem
cabe no centavo de tolerância. Se um dia a planilha trouxer um `capex` que não seja
`quantidade × preco_unitario`, é a CARGA que passa a falhar — e falhar é o certo,
porque o motor ignora esse número de qualquer forma
(`otimizador_capex_v62.py:1165`).

### As que faltam, e o que cada uma destrava

1. **`reprocessa_de` em `controle.run_request`.** A reexecução depois de um
   `SUCESSO` gera `run_id` novo (`CONTRATO.md` §2.1). Sem esse campo o histórico é
   uma lista de rodadas soltas, sem como ligar a reexecução à sua origem.

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
