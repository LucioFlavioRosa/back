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
                     005_capex_derivado.sql — o banco recusa CAPEX que não seja
                     quantidade × preco_unitario
                     006_auditoria_cadastro.sql — última alteração e autor por
                     ficha; substituiu o 409 de escrita
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
- ~~**`_BASE_SUBBACIA`** em `cadastro_escrita.py` é cópia de `BASE_OBRAS` do
  front~~ — **resolvido**, e não do jeito que este item propunha. A saída era
  "o backend servir a base para a tela"; a certa era **não haver base**. As duas
  listas saíram: a obra é materializada da linha gravada em
  `componentes_*_capex`, o `GET` manda `nome` e `un`, e ficha sem o componente
  vira **recusa** (422) em vez de preenchimento com valores de template.
  `GET /prontidao` → `faltando[]` diz qual componente falta, que é o que a tela
  não teria como saber. Ver `tests/test_obras_do_banco.py`.
- **Validação do token do Entra ID** (`app/api/deps.py`): falta o JWKS do tenant.
  Está levantando erro em vez de decodificar sem verificar, de propósito.
- **Cancelar rodada**: bloqueado por migração (ver abaixo).
- **Nome da rodada**: o job já publica `rotulo` e `usuario` em `otim_meta` (5/5
  das rodadas carregadas têm os dois). O que ainda falta é `rotulo` e
  `reprocessa_de` em `controle.run_request`, para o nome sobreviver ao
  reprocessamento — hoje ele só existe depois que a rodada publica.
- **`progresso` do status**: a coluna foi criada (`migracoes/002_progresso.sql`) e o
  endpoint a serve. **Aplique a migração antes de subir** — sem ela a consulta de
  status falha. Falta o JOB escrevê-la; `dev/worker.py` já escreve, nas mesmas
  faixas que o front usa para nomear a etapa.
> Este parágrafo dizia **"nada disto foi executado contra um Postgres real"**.
> Era verdade quando foi escrito e deixou de ser: hoje o `docker-compose` sobe um
> Postgres 16, `dev/recarregar_tudo.py` carrega as 5 unidades da planilha e roda as
> 5 simulações, e os smokes de `dev/` batem nos endpoints contra esse banco. Três
> dos erros mais caros do repositório só apareceram assim — nome de coluna errado,
> `jsonb` chegando como texto, e um handler de erro que estourava em vez de
> responder. Fica registrado porque a frase enganou por semanas.

## Uma decisão de modelagem: a CTS não se cria pela tela

`POST /cts` e `DELETE /cts` **foram removidos**, e o `DEPLOY.md` §3 do front ainda
os promete — está pendente lá.

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

A tela e o `DEPLOY.md` §3 já acompanharam: os dois botões saíram, junto dos hooks e
dos cinco testes que exercitavam a funcionalidade retirada.

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
nos dois — continua editável, e agora se sabe que a simulação não a vê — enquanto
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
5. ~~**`capex` derivado**~~ — **feita** (`migracoes/005_capex_derivado.sql`).
   Mesma condição do item 3: aplicar nos bancos existentes e dobrar no
   `ddl_input.sql` do repositório do otimizador. **E há uma segunda ponta lá:** o
   `carregar_postgres.py` manda a coluna `capex` da planilha, e a constraint a
   aceita só porque o arredondamento da origem cabe no centavo de tolerância. Se
   um dia a planilha trouxer um `capex` que não seja `quantidade × preco_unitario`,
   é a CARGA que passa a falhar — e falhar é o comportamento certo, porque o motor
   ignoraria esse número de qualquer forma
   (`otimizador_capex_v62.py:1165`).
6. ~~**Auditoria de cadastro**~~ — **feita** (`migracoes/006_auditoria_cadastro.sql`).
   `atualizado_em`/`atualizado_por` nas quatro tabelas de ficha. Mesma condição
   dos itens 3 e 5: aplicar nos bancos existentes e dobrar no `ddl_input.sql`.
   **Não quebra a carga**: a planilha não tem essas colunas, e o carregador só
   manda as que existem nos dois lados. O que ela quebra, se faltar, é o
   `/readyz` — de propósito. Ela substituiu o 409 de ficha, então um pod que sobe
   sem ela deixa a escrita sem proteção **e** sem aviso.

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
