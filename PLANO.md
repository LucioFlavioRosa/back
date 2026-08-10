# Plano de trabalho — o que fazer a seguir

Documento de passagem. Escrito no fim de uma conversa longa, para a próxima
começar com o contexto certo em vez de reconstruí-lo.

> **Leia `PLANO-REVISAO.md` antes de codar.** O Codex revisou este plano com
> acesso ao ambiente e **a ordem numerada abaixo está errada**. A ordem certa é:
>
> ```
> 7  →  1+2 juntos  →  3+4 juntos  →  6  →  5
> ```
>
> Os motivos, um por linha:
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

- **R1 — não cumprida.** `_BASE_SUBBACIA`/`_BASE_CTS` ainda recriam obra a partir
  de literal quando falta componente. Reproduzido: `PUT` gerou
  `Linha de recalque (LR) | qtd 0 | preco 900 | dur 15 | wacc 0,067`.
- **R2 — não cumprida.** Mesmo caminho de R1.
- **R3 — parcial.** Trava certo (prontidão 0 → 7 ao apagar uma obra; `POST /runs`
  responde 422), mas a tela mostra só o **número**, não *qual* falta.
- **R4 — cumprida.** Única escrita em `otim_*` fora da publicação é o `DELETE`,
  com cascata. Testado: `1|1|1` → `204` → `0|0|0`.
- **R5 — parcial.** Dedupe já considera o usuário. Falta: **mensagem** na tela (hoje
  volta 200 em silêncio) e alcançar as **concluídas** (hoje só `PENDENTE`/`RODANDO`).
- **R6 — não feita.**

---

## O plano

### 1. Auditoria de cadastro  *(R6)*

- Migração `migracoes/005_auditoria_cadastro.sql`: `atualizado_em timestamptz` e
  `atualizado_por text` em `subbacia_operacional`, `cts_operacional`,
  `ete_capex`, `cidade_operacional`.
- O `PUT` grava as duas em toda gravação, com o usuário **do token** — nunca do
  corpo. (`cadastro_escrita.py`; o padrão de autoria já existe em
  `_gravar_overrides`.)
- O `GET` devolve junto da ficha; a tela mostra algo como
  *"última alteração: ana@aegea, 10/08 14:32"*.
- Acrescentar as colunas à lista `_EXIGIDO` em `app/infra/db.py`, para o
  `/readyz` recusar o pod se a migração não rodou.

### 2. Remover o 409 de ficha  *(R6 substitui)*

Não confundir com a dedupe de simulação, que **fica**.

- **Backend:** `versao` do payload, `_exigir_versao`, `_versao_atual`, e o campo
  no retorno dos três `salvar_*`.
- **Front:** `versao` dos quatro tipos de domínio, de `ComOverrides`, de
  `fichas.ts`, do `FICHA_SALVA` no reducer, e `conferirContrato` em
  `mutations.ts`.
- **Saem junto:** `dev/smoke_versao.py`, o bloco "o ciclo da versao" em
  `escrita.test.tsx`, `apiFake.putSemVersao`, e o `versao` das fixtures.
- O `CONTRATO.md`/`DEPLOY.md` mencionam 409 na escrita — atualizar.

### 3. Tirar os literais de obra  *(R1, R2)* — **o mais arriscado**

- Backend: apagar `_BASE_SUBBACIA`/`_BASE_CTS`. `_obras_da_ficha` materializa
  **só** de `componentes_*_capex`; faltando componente, **recusa**.
- Front: apagar `BASE_OBRAS`/`BASE_OBRAS_CTS`. O `GET` passa a mandar `nome` e
  `un` (estão no banco e hoje não são enviados), e `mkObras` usa o que veio.
- Sai junto: `tests/test_base_obras.py`, que existe só para comparar as duas
  bases, e o `APELIDOS` dele.
- **Confirmar antes:** hoje toda ficha tem 5 componentes (sub) e 4 (CTS) —
  medido `min=max`. O front usa a base para montar a tabela **e** para decidir o
  que é override; os dois usos precisam de substituto.

### 4. A tela dizer o que falta  *(R3)*

- `/prontidao` passa a devolver as pendências **por ficha** (sub-bacia, CTS, ETE,
  cidade), não só o total por grupo.
- A tela lista: *"sub-bacia a1b25_1_1 — falta o componente Coletor tronco"*.
- Mesmo padrão da denúncia de CTS inconsistente, que já funciona assim
  (`GET /cts` → `inconsistencias[]`).

### 5. Dedupe alcançar as concluídas  *(R5)*

- `controle.rodada_em_voo` filtra `status IN ('PENDENTE','RODANDO')`. Passa a
  considerar também `SUCESSO`. **`ERRO` continua liberando nova execução** — quem
  repete depois de uma falha está corrigindo, e apontar para o fracasso anterior
  impediria a correção.
- O front precisa **distinguir 201 de 200** e mostrar a mensagem: *"já existe uma
  simulação idêntica a esta"*, com link. Hoje ele navega em silêncio.

### 6. Limpar as rodadas de teste

`otim_meta` tem 27 rodadas, `run_request` 16 — com `dev@local`, `smoke`, `u1`,
`u_par` e 7 em `ERRO`. Poluem histórico e auditoria. Ou limpar, ou separar o
banco e2e do banco com dado real.

### 7. Decidir o `capex`

Sete linhas com precisão diferente da planilha (`204866.2556` × `204866.26`).
Hoje está **misto**: em alguns lugares armazenado, em outros derivado
(`quantidade × preco_unitario`, calculado no servidor). Precisa de uma regra só.

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
