# Plano de trabalho — o que fazer a seguir

Documento de passagem. Escrito no fim de uma conversa longa, para a próxima
começar com o contexto certo em vez de reconstruí-lo.

> ## Os sete itens estão feitos.
>
> A numeração original estava errada, e o `PLANO-REVISAO.md` a corrigiu antes de
> qualquer código. Foram aplicados nesta ordem, e cada seção abaixo conta o que
> entrou:
>
> ```
> 7  →  1+2 juntos  →  3+4 juntos  →  6  →  5
> ```
>
> Os motivos da ordem, um por linha — valem como registro de por que agrupar
> importava:
>
> - **7 antes de 3** — decidir o `capex` muda a leitura e a escrita de
>   `componentes_*_capex`, que é justamente o que o item 3 reescreve.
> - **1 e 2 juntos** — mexem no mesmo `PUT`. Remover o `versao` antes de entregar
>   a auditoria cria *last-write-wins sem nenhum sinal visível*: perde a proteção
>   e não entrega a compensação que a R6 pede.
> - **3 e 4 juntos** — tirar o literal sem já mostrar *qual componente faltou*
>   piora a experiência da R3 em vez de melhorar.
> - **6 antes de validar o 5** — há `SUCESSO` em `controle` sem publicação em
>   `otim_meta`, e isso polui a dedupe se a consulta for feita errado.
>
> E uma correção ao item 3: o `test_base_obras.py` **não deve simplesmente sair**.
> Deve virar teste de cardinalidade contra o banco — sub-bacia = 5, CTS = 4, nomes
> esperados, e recusa quando falta componente.

---

## As regras do dono do produto

Tudo abaixo existe para servir a estas seis. **Se uma tarefa não serve a
nenhuma, ela não deveria estar no plano** — foi assim que a bagunça começou da
última vez.

| | regra |
|---|---|
| **R1** | O banco SQL é a **verdade absoluta**. Nenhum valor nasce de constante no código. |
| **R2** | **Não inventar nem inserir nada.** Nenhuma gravação de valor que o usuário não digitou e que não veio do banco. |
| **R3** | Dado faltando: **avisar e travar a simulação da unidade inteira**. Exceção única: `wacc` ausente usa o WACC da unidade. |
| **R4** | Só **cadastro** é editável. Simulação, uma vez escrita, só pode ser **excluída**. |
| **R5** | Dedupe de simulação por `run_id` + **usuário**. Mesmo usuário + pedido idêntico → **mensagem** apontando para a simulação existente. Usuários distintos → rodam as duas. Vale para rodada **em voo e concluída**; rodada em **`ERRO` libera nova execução**. |
| **R6** | Mostrar **última atualização e autor** no cadastro. Substitui o 409 de ficha. |

### Estado de cada regra (medido)

- **R1 — cumprida.** As quatro bases literais saíram (duas no backend, duas no
  front). A obra é materializada da linha gravada; ficha incompleta é recusa.
  Guarda-corpo contra a volta: `tests/test_obras_do_banco.py`.
- **R2 — cumprida.** Mesmo caminho de R1. E o `capex` deixou de ser inventado
  quando falta fator: virou nulo em vez de zero (item 7).
- **R3 — cumprida.** Trava (prontidão 0 → 7 ao apagar uma obra; `POST /runs`
  responde 422) **e** diz o quê: `/prontidao` → `faltando[]` nomeia o componente
  e a ficha, e o checklist da simulação o mostra linha a linha.
- **R4 — cumprida.** Única escrita em `otim_*` fora da publicação é o `DELETE`,
  com cascata. Testado: `1|1|1` → `204` → `0|0|0`.
- **R5 — cumprida.** Dedupe considera o usuário, alcança a rodada **concluída**
  (publicada e posterior à última alteração do cadastro), `ERRO` continua
  liberando execução nova, e a tela **avisa com link** em vez de navegar em
  silêncio.
- **R6 — cumprida.** As quatro fichas trazem `atualizadoEm`/`atualizadoPor`, o
  `PUT` carimba com o usuário do token, e a tela mostra a linha. Medido: `PUT`
  com `atualizadoPor` forjado no corpo grava o autor do token, e não o do corpo.

---

## O plano

### ~~1 + 2. Auditoria de cadastro, e o 409 de ficha sai~~ — **feito**

Entraram juntos, como a revisão exigia: remover o `versao` antes de entregar a
auditoria criaria *last-write-wins sem nenhum sinal visível*.

**A troca, em uma frase:** o servidor não recusa mais a gravação de quem leu a
ficha antes de um colega salvar — ele registra quem gravou, e a tela mostra.

O 409 comparava o hash da ficha INTEIRA. Quem abriu de manhã e salvou à tarde
perdia o trabalho por causa de um colega que mexeu em OUTRO campo da mesma ficha:
cobrava o preço de um conflito onde quase nunca havia um.

**O que se perde, dito sem enfeite:** duas pessoas na mesma ficha ainda se
sobrescrevem, e agora sem aviso no momento da gravação. O sinal virou posterior e
legível. Se um dia isso precisar ser barrado de novo, o caminho **não** é
ressuscitar o hash da ficha inteira — é comparar por CAMPO.

Backend:

- `migracoes/006_auditoria_cadastro.sql`: `atualizado_em timestamptz` e
  `atualizado_por text` nas quatro tabelas de ficha. Sem `DEFAULT now()`, de
  propósito: carimbaria a data da migração em 4.850 sub-bacias que ninguém tocou.
- `_marcar_autoria` no lugar de `_exigir_versao`/`_versao_atual`. Roda em TODA
  gravação, com o autor **do token**, e **devolve o carimbo** na resposta do
  `PUT` — sem isso a ficha exibiria a alteração anterior logo depois de você
  salvar.
- Saíram: `FichaDesatualizada`, o handler de 409 em `api/erros.py`, `versao()` em
  `cadastro.py`, `dev/smoke_versao.py`. O 409 de **simulação** fica.
- `dev/legado_seed/smoke_conflito.py` virou `smoke_auditoria.py`: prova o que
  existe agora, inclusive que **autor vindo no corpo é ignorado**.
- `_EXIGIDO` cobre as quatro tabelas — o `/readyz` recusa o pod sem a migração.

Front:

- `domain/auditoria.ts` (novo): o tipo, o formatador e o `auditoriaDe`, que
  extrai só os dois campos da resposta. Os quatro tipos de ficha o estendem.
- `components/UltimaAlteracao.tsx` (novo): a linha *"última alteração: ana@aegea,
  10/08 14:32"*. Nas quatro telas — duas via `RecordSheet`, duas no
  `GrupoHeader`, que são caminhos de renderização diferentes.
- `erroAoSalvar.ts` perdeu o ramo do 409 e virou toast. O fluxo de **recarregar
  do servidor** sobreviveu no outro gatilho que sempre teve: rascunho local sobre
  dado que mudou no servidor — e os dois casos que só o bloco do 409 cobria
  mudaram de gatilho em vez de sumir.
- Fixtures atualizadas no mesmo passo (a lição do fim deste documento).

**Uma armadilha que quase passou:** as páginas passavam a resposta INTEIRA do
`PUT` como auditoria, e o spread levava `id`/`overridesGravados` para dentro da
ficha **sem trocar a auditoria** — a tela seguia creditando a gravação a quem
salvara antes. Quem pegou foi o teste do servidor 2xx sem auditoria. Daí o
`auditoriaDe`, e um teste próprio para ele.

### ~~3 + 4. Tirar os literais de obra, e a tela dizer o que falta~~ — **feito**

Entraram juntos, como a revisão exigia: tirar a base sem mostrar *qual*
componente faltou pioraria a R3 em vez de melhorar.

**As duas bases literais não existem mais.** O que elas produziam, medido antes:
um `PUT` numa ficha sem o componente gravado escrevia `Linha de recalque (LR) |
qtd 0 | preco 900 | dur 15 | wacc 0,067`. Nenhum daqueles números veio do banco
nem de alguém digitando, e iam para a simulação com cara de cadastro. Corrupção
silenciosa é pior que perda silenciosa: a plausibilidade impede a desconfiança.

Backend:

- `_BASE_SUBBACIA`/`_BASE_CTS` apagadas. `_obras_da_ficha` materializa só de
  `componentes_*_capex` e **recusa** (422) a ficha com menos componentes que a
  régua — a mesma régua do `/prontidao` (`OBRAS_SUBBACIA`/`OBRAS_CTS`), para a
  tela e o `PUT` nunca discordarem sobre o mesmo estado.
- O `GET` passou a mandar `nome`, e com isso cada tabela conserva o vocabulário
  dela: `componentes_cts_capex` chama `Tronco` o que a sub-bacia chama `Coletor
  tronco`, e a base literal — que usava o vocabulário da sub-bacia nas duas —
  regravava a CTS com nomes que o motor não reconhece.
- `/prontidao` ganhou `faltando[]`, no padrão do `inconsistencias[]` de
  `GET /cts`. **Os nomes esperados não são literal:** saem do `DISTINCT
  componente` da própria tabela — "o que as outras 4.849 fichas têm".

Front:

- `BASE_OBRAS`/`BASE_OBRAS_CTS` apagadas; `mkObras` monta a linha só do que veio,
  e campo ausente fica **vazio** (que conta pendência) em vez de preenchido.
- `OBRAS_POR_SUBBACIA`/`OBRAS_POR_CTS` ficaram, e são cardinalidade, não valor:
  servem para obra que FALTA pesar como obra em branco. Sem isso a ficha com
  quatro componentes se declarava completa — o que não veio não tem campo vazio
  para contar.
- `withObraOverride` não apaga mais campo: o mapa carrega a obra inteira, e
  apagar criaria buraco. O "digitou de volta o original" continua funcionando
  pela comparação de conteúdo, que sempre foi quem respondia isso.
- O checklist da simulação lista *"sub-bacia a1b25_1_1 — falta o componente
  Coletor tronco"*, cortando em 5 e dizendo quantas ficaram de fora.

Testes: `tests/test_base_obras.py` virou `tests/test_obras_do_banco.py`, como a
revisão pediu — cardinalidade e nomes **contra o banco real** (pulado sem
Postgres), mais um guarda-corpo contra a base voltar dos dois lados. As fixtures
do front foram materializadas (`BASE ⊕ override`), que é o que o servidor manda
agora.

**Medido ponta a ponta:** apagando `Coletor tronco` de `b1b25_1_1`, o
`/prontidao` nomeia o componente, o `GET` devolve 4 obras sem inventar a quinta,
e o `PUT` responde 422 apontando para o `/prontidao`. Componente restaurado
depois.

### ~~5. Dedupe alcançar as concluídas~~ — **feito**

`rodada_em_voo` virou `rodada_identica` — o nome antigo passou a mentir no
instante em que ela deixou de olhar só o que está em voo.

**Três condições para uma concluída deduplicar**, e a terceira é a que a revisão
apontou e que só ficou possível depois do item 1:

1. **`SUCESSO`** — `ERRO` continua liberando execução nova. Quem repete depois de
   uma falha está corrigindo algo, e apontá-lo para o fracasso anterior impediria
   a correção.
2. **publicada em `otim_meta`** — `SUCESSO` sem resultado é um estado que mente, e
   mandar alguém para ele é prometer uma tela vazia.
3. **posterior à última alteração do cadastro** — os mesmos parâmetros de TELA não
   são a mesma simulação se o CADASTRO mudou no meio: a rodada de ontem leu preços
   e obras que não são os de hoje. A conta usa `atualizado_em`, que **só existe
   desde a auditoria por ficha** (item 1). Antes dela não havia como fazer essa
   pergunta — e sem ela a dedupe violaria a R1.

Compara com `solicitado_em`, e não com a hora da publicação: é o instante em que a
rodada começou a ler o cadastro. Alteração feita DURANTE a execução deixa
`solicitado_em` anterior a ela e, corretamente, libera rodada nova.

O limite, dito: só enxerga alteração que passou pelo `PUT`. Carga de planilha e SQL
solto não carimbam nada — depois deles a régua é recarregar o banco.

Front: o `POST /runs` devolve `jaExistia` **no corpo** (o cliente descarta o código
HTTP), e a tela distingue os dois casos. Concluída → aviso com link, sem abrir o
modal de acompanhamento de algo que terminou ontem. Em voo → segue acompanhando,
que é o duplo clique levando ao mesmo lugar.

**Medido contra o banco real**, com uma rodada concluída plantada e removida
depois: pedido idêntico → `200 {jaExistia: true, status: SUCESSO}`; outro usuário
→ não deduplica; cadastro alterado depois → não deduplica; carimbo restaurado →
volta a deduplicar.

### ~~6. Limpar as rodadas de teste~~ — **feito**

Limpo, e não separando os bancos: `dev/limpar_rodadas_de_teste.py`, script
explícito, **em modo relatório por padrão** — só escreve com `--apagar`.

Como a revisão exigiu, **nada disto virou lógica de aplicação**. O serviço não
decide sozinho que uma rodada é de teste: rodada é imutável (R4), e a única
exclusão que o produto oferece continua sendo a que uma pessoa pede, uma por vez,
pelo `DELETE /runs/{id}` — cuja ordem de exclusão o script reusa.

Cinco regras, todas estreitas: unidade sintética (`u1`/`u_par`), autor que não é
pessoa (`smoke`, `u1`, `u_par`), `ERRO`, `SUCESSO` sem publicação, e repetição do
laço de tela (mesmo autor + mesma regional + mesmo rótulo, guardando a mais
recente). **`dev@local` não é critério** — é a identidade de qualquer um com a
autenticação desligada, inclusive nas rodadas boas; o que denuncia o laço é o
rótulo repetido, não quem disparou.

Resultado medido: fila **20 → 6**, publicadas **27 → 9**, zero linha órfã nas 13
tabelas de resultado (a cascata do `otim_meta` deu conta). Ficaram as 3 rodadas
de referência do `lucio.rosa`, uma `dev@local` por unidade, e as de
ana/bruno/carlos.

**O que isto destrava para o item 5:** toda rodada que sobrou na fila é `SUCESSO`
E está publicada. Os dois `SUCESSO` órfãos que existiam eram de `u1` — e eram
exatamente o que faria a dedupe de rodada concluída apontar para um sucesso sem
resultado.

### ~~7. Decidir o `capex`~~ — **feito**

**A regra: `capex` é DERIVADO — `quantidade × preco_unitario`, calculado pelo
servidor, e o banco recusa quem discordar.**

A decisão não foi tomada aqui: o motor já a tinha tomado. Em
`otimizador_capex_v62.py:1165` — *"CAPEX pode vir DECOMPOSTO em quantidade x
preco unitario; se vier, ele manda"* —, e a linha 1192 loga aviso quando a coluna
diverge. O cadastro guardava um número que a simulação ignorava.

Medido antes de mexer: 24.250 componentes de sub-bacia e 1.348 de CTS, **nenhum**
sem os dois fatores — a derivação sempre se aplica. As sete linhas do texto
antigo eram as fichas `b1b25_1_1` e `e1b25_1_1`, tocadas por `PUT` em teste.
Outras 205 divergiam da multiplicação em exatamente R$ 0,005: arredondamento da
planilha, não opinião.

O que entrou:

- `migracoes/005_capex_derivado.sql`: constraint `capex_e_derivado` nas duas
  tabelas, tolerando um centavo. **Este número tomou o 005 — a auditoria do item
  1 passa a ser `006_auditoria_cadastro.sql`.**
- `_gravar_obras` chama `_capex()`, função pura e testada
  (`tests/test_capex_derivado.py`). Saiu o `or 0` que transformava fator ausente
  em CAPEX zero — valor que ninguém digitou, com cara de cadastro preenchido.
  Agora vira nulo, e a falta do fator já é pendência que trava a unidade.
- `_EXIGIDO_RESTRICAO` em `app/infra/db.py`: o `/readyz` recusa o pod sem a
  migração. Checar a coluna não serviria — ela já existia.

**Por que CHECK e não `GENERATED ALWAYS`**, que seria mais forte: a coluna gerada
recusa `INSERT` que mencione `capex`, e o carregador de produção
(`carregar_postgres.py`, no repositório do otimizador) manda a coluna da planilha.
Seria quebrar a carga de produção a partir de um repositório que não é dono do
esquema. O CHECK deixa o arredondamento da origem passar e recusa uma segunda
opinião de verdade.

**O que NÃO foi feito, de propósito:** reescrever as 205 linhas para a precisão
cheia. Meio centavo em valores de milhão, num número que o motor não lê — seria
escrita em dado real para não ganhar nada. Elas se corrigem sozinhas no dia em
que a ficha for salva.

---

## Ambiente

```bash
# a partir de otimizador-backend
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
python dev/recarregar_tudo.py          # apaga e recarrega da planilha (~20 min)
python dev/worker.py --paralelo 2 --tempo 30   # consome a fila (o job local)
```

- front `http://localhost:8080` · API `:8000` e `:8080/api`
- `X-Usuario-Dev: ana@aegea` troca de identidade — **só** com autenticação
  desligada. Perfis semeados em `controle.usuario_acesso`.
- `python dev/conferir_planilha.py` — o banco reproduz a planilha? **Rode depois
  de qualquer teste que escreva**, para não deixar dado adulterado.

## Portão

```bash
PYTHONPATH=. python -m pytest -q                    # backend
python dev/smoke.py dev/formas.py dev/smoke_incons.py   # contra o banco real
npx vitest run && npx tsc --noEmit && npx eslint src --max-warnings 0 && npx knip
```

---

## Duas lições desta rodada, que valem mais que o plano

**Fixture que não espelha o payload real testa um produto que não existe.** Três
bugs passaram por isso: a `versao` ausente, as colunas de população, e o
`publicada` que derrubou a tela de histórico. Ao mudar a forma de uma resposta,
mude a fixture no mesmo commit.

**Comentário que mente conta como defeito.** Esta base explica muito em
comentário e as pessoas confiam. Já custou caro: `pendencias.py` dizia
*"wacc NUNCA conta"* enquanto a lista abaixo cobrava.
