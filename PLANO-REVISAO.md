# Revisão do PLANO.md pelo Codex

> Gerada antes de implementar, com acesso ao ambiente. Critério: as seis regras
> do dono do produto. Leia junto do `PLANO.md` — ela diz se a ordem tem
> retrabalho e o que quebra em cada item.

A. **Ordem**

Não está na melhor ordem.

1. `input.*` tabelas de cadastro: faça o item 7 antes do 3. `capex` hoje é coluna SQL e também é recalculado no backend/front; decidir se é armazenado ou derivado muda a leitura/escrita de `componentes_*_capex`.
2. `app/infra/repositorios/cadastro_escrita.py:494` e `:518`: itens 1 e 2 mexem no mesmo PUT. Devem entrar juntos, não sequenciais longos. Se remover `versao` antes de entregar `atualizado_em/por`, você cria last-write-wins sem nenhum sinal visível.
3. `app/infra/repositorios/cadastro_escrita.py:577` e `src/cadastro/domain/subbacia.ts:149`: itens 3 e 4 devem ser um pacote. Remover base literal sem já devolver/mostrar “qual componente faltou” piora a UX da regra R3.
4. Item 6 deve vir antes de validar item 5 no banco atual. Há `SUCESSO` em `controle` sem publicação em `public.otim_meta`; isso pode poluir a dedupe se a query for feita errado.

Ordem sugerida: 7 -> 1+2 juntos -> 3+4 juntos -> 6 -> 5 -> testes/contrato.

B. **Itens Errados Ou Desnecessários**

`tests/test_base_obras.py`: errado simplesmente “sair”. O teste atual que compara literal com front perde sentido, mas deve virar teste contra ausência de literal/cardinalidade do banco: sub-bacia = 5, CTS = 4, nomes esperados e recusa quando falta componente.

`item 6`: não é requisito de produto, é higiene de ambiente. Faça com script/SQL explícito e restrito a `dev@local`, `u1`, `u_par`, `smoke` e `ERRO`; não transforme em lógica de aplicação.

`item 2`: correto para R6, mas incompleto se for só remover `409`. Precisa substituir por auditoria visível no cadastro. Sem isso, remove proteção e não entrega a compensação pedida pelo dono.

C. **Item 3**

Confirmado no banco atual: não falta componente.

`tabela input.componentes_subbacias_capex`: 4.850 fichas com 5 componentes cada, por unidade `uA1/uA2/uA3/uB1/uB2`.

`tabela input.componentes_cts_capex`: 337 fichas com 4 componentes cada, por unidade `uB1/uB2`.

Componentes atuais:
`sub`: `Ligacao de esgoto`, `Rede coletora`, `Coletor tronco`, `Estacao elevatoria (EEE)`, `Linha de recalque (LR)`.
`cts`: `Coletor de tempo seco`, `Tronco`, `EEE`, `Linha de recalque`.

O que quebra se faltar depois:

`app/infra/repositorios/pendencias.py:43`: obra ausente já conta como pendência; a unidade deve travar.

`app/api/simulacao.py:85`: o POST `/runs` trava, mas hoje a mensagem só diz quantidade, não qual ficha/componente.

`app/infra/repositorios/cadastro.py:550`: GET ignora componente fora do de-para; se faltar índice, o front atual completa com `BASE_OBRAS`, inventando linha.

`src/cadastro/domain/subbacia.ts:158` e `src/cadastro/domain/cts.ts:93`: o front monta tabela aplicando override sobre base literal. Sem base, ele precisa receber a lista materializada do backend ou um erro estruturado; senão não sabe quantas linhas renderizar nem o que é override.

D. **Concorrência Sem 409**

Sim, sobra caminho de sobrescrita silenciosa.

`app/infra/repositorios/cadastro_escrita.py:554`: o lock serializa os PUTs, mas sem `_exigir_versao` ele não detecta conflito; só garante ordem.

`app/infra/repositorios/cadastro_escrita.py:346`: sub/CTS regravam obras com `DELETE + INSERT`; duas pessoas salvando a ficha inteira resultam em last-write-wins.

`app/infra/repositorios/cadastro_escrita.py:625` e `:638`: cidade apaga/reinsere metas e fator; segunda gravação pode apagar mudanças da primeira.

`app/infra/repositorios/cadastro_escrita.py:720`: ETE faz upsert dos campos presentes; conflito vira sobrescrita de campo.

Impacto: duas pessoas podem sobrescrever uma à outra sem alerta no momento do salvamento. Com item 1, só fica perceptível depois, no cadastro, como “última atualização por X em Y”.

E. **Dedupe Concluída**

Muda em `app/infra/repositorios/controle.py:147`.

Hoje filtra só:

`r.unidade = $1 AND s.status IN ('PENDENTE','RODANDO')`.

A versão correta não deve ser “todo `SUCESSO`”. Deve incluir concluída publicada/consultável:

```sql
WHERE r.unidade = $1
  AND (
    s.status IN ('PENDENTE','RODANDO')
    OR (s.status = 'SUCESSO' AND EXISTS (
      SELECT 1 FROM public.otim_meta m WHERE m.run_id = r.run_id
    ))
  )
```

`app/infra/repositorios/controle.py:115`: `USUARIO` já entra no digest via `params`, então usuários distintos não deduplicam.

Risco real: se o cadastro mudou depois da rodada concluída, os mesmos parâmetros de tela não representam a mesma simulação. Para não violar R1, a identidade do pedido precisa incluir uma revisão/snapshot do cadastro, ou a dedupe de concluídas só pode apontar para rodada existente quando ela é posterior à última atualização do cadastro da unidade.

F. **Falta Algo Para R1..R6**

Falta detalhamento de pendências no backend. `app/api/simulacao.py:87` só devolve número; item 4 fala da tela, mas R3 exige avisar. A API precisa devolver ficha/componente/campo faltante.

Falta usar `atualizado_em/por` como revisão da unidade para dedupe de concluídas. Sem isso, item 5 pode bloquear reexecução legítima após alteração de cadastro.

Contradição atual: `src/cadastro/domain/subbacia.ts:149` e `src/cadastro/domain/cts.ts:85` ainda têm base literal no front, contra R1/R2.

Contradição atual: `app/infra/repositorios/cadastro_escrita.py:238` e `:261` ainda têm base literal no back, contra R1/R2.

Contradição atual: `app/api/simulacao.py:187` ainda expõe rota de cancelar, mas o próprio comentário diz que não está implementada por falta de status no banco. Para R4, manter endpoint morto é ruído; ou remove do contrato/front, ou implementa com regra explícita.
tokens used
114.446
A. **Ordem**

Não está na melhor ordem.

1. `input.*` tabelas de cadastro: faça o item 7 antes do 3. `capex` hoje é coluna SQL e também é recalculado no backend/front; decidir se é armazenado ou derivado muda a leitura/escrita de `componentes_*_capex`.
2. `app/infra/repositorios/cadastro_escrita.py:494` e `:518`: itens 1 e 2 mexem no mesmo PUT. Devem entrar juntos, não sequenciais longos. Se remover `versao` antes de entregar `atualizado_em/por`, você cria last-write-wins sem nenhum sinal visível.
3. `app/infra/repositorios/cadastro_escrita.py:577` e `src/cadastro/domain/subbacia.ts:149`: itens 3 e 4 devem ser um pacote. Remover base literal sem já devolver/mostrar “qual componente faltou” piora a UX da regra R3.
4. Item 6 deve vir antes de validar item 5 no banco atual. Há `SUCESSO` em `controle` sem publicação em `public.otim_meta`; isso pode poluir a dedupe se a query for feita errado.

Ordem sugerida: 7 -> 1+2 juntos -> 3+4 juntos -> 6 -> 5 -> testes/contrato.

B. **Itens Errados Ou Desnecessários**

`tests/test_base_obras.py`: errado simplesmente “sair”. O teste atual que compara literal com front perde sentido, mas deve virar teste contra ausência de literal/cardinalidade do banco: sub-bacia = 5, CTS = 4, nomes esperados e recusa quando falta componente.

`item 6`: não é requisito de produto, é higiene de ambiente. Faça com script/SQL explícito e restrito a `dev@local`, `u1`, `u_par`, `smoke` e `ERRO`; não transforme em lógica de aplicação.

`item 2`: correto para R6, mas incompleto se for só remover `409`. Precisa substituir por auditoria visível no cadastro. Sem isso, remove proteção e não entrega a compensação pedida pelo dono.

C. **Item 3**

Confirmado no banco atual: não falta componente.

`tabela input.componentes_subbacias_capex`: 4.850 fichas com 5 componentes cada, por unidade `uA1/uA2/uA3/uB1/uB2`.

`tabela input.componentes_cts_capex`: 337 fichas com 4 componentes cada, por unidade `uB1/uB2`.

Componentes atuais:
`sub`: `Ligacao de esgoto`, `Rede coletora`, `Coletor tronco`, `Estacao elevatoria (EEE)`, `Linha de recalque (LR)`.
`cts`: `Coletor de tempo seco`, `Tronco`, `EEE`, `Linha de recalque`.

O que quebra se faltar depois:

`app/infra/repositorios/pendencias.py:43`: obra ausente já conta como pendência; a unidade deve travar.

`app/api/simulacao.py:85`: o POST `/runs` trava, mas hoje a mensagem só diz quantidade, não qual ficha/componente.

`app/infra/repositorios/cadastro.py:550`: GET ignora componente fora do de-para; se faltar índice, o front atual completa com `BASE_OBRAS`, inventando linha.

`src/cadastro/domain/subbacia.ts:158` e `src/cadastro/domain/cts.ts:93`: o front monta tabela aplicando override sobre base literal. Sem base, ele precisa receber a lista materializada do backend ou um erro estruturado; senão não sabe quantas linhas renderizar nem o que é override.

D. **Concorrência Sem 409**

Sim, sobra caminho de sobrescrita silenciosa.

`app/infra/repositorios/cadastro_escrita.py:554`: o lock serializa os PUTs, mas sem `_exigir_versao` ele não detecta conflito; só garante ordem.

`app/infra/repositorios/cadastro_escrita.py:346`: sub/CTS regravam obras com `DELETE + INSERT`; duas pessoas salvando a ficha inteira resultam em last-write-wins.

`app/infra/repositorios/cadastro_escrita.py:625` e `:638`: cidade apaga/reinsere metas e fator; segunda gravação pode apagar mudanças da primeira.

`app/infra/repositorios/cadastro_escrita.py:720`: ETE faz upsert dos campos presentes; conflito vira sobrescrita de campo.

Impacto: duas pessoas podem sobrescrever uma à outra sem alerta no momento do salvamento. Com item 1, só fica perceptível depois, no cadastro, como “última atualização por X em Y”.

E. **Dedupe Concluída**

Muda em `app/infra/repositorios/controle.py:147`.

Hoje filtra só:

`r.unidade = $1 AND s.status IN ('PENDENTE','RODANDO')`.

A versão correta não deve ser “todo `SUCESSO`”. Deve incluir concluída publicada/consultável:

```sql
WHERE r.unidade = $1
  AND (
    s.status IN ('PENDENTE','RODANDO')
    OR (s.status = 'SUCESSO' AND EXISTS (
      SELECT 1 FROM public.otim_meta m WHERE m.run_id = r.run_id
    ))
  )
```

`app/infra/repositorios/controle.py:115`: `USUARIO` já entra no digest via `params`, então usuários distintos não deduplicam.

Risco real: se o cadastro mudou depois da rodada concluída, os mesmos parâmetros de tela não representam a mesma simulação. Para não violar R1, a identidade do pedido precisa incluir uma revisão/snapshot do cadastro, ou a dedupe de concluídas só pode apontar para rodada existente quando ela é posterior à última atualização do cadastro da unidade.

F. **Falta Algo Para R1..R6**

Falta detalhamento de pendências no backend. `app/api/simulacao.py:87` só devolve número; item 4 fala da tela, mas R3 exige avisar. A API precisa devolver ficha/componente/campo faltante.

Falta usar `atualizado_em/por` como revisão da unidade para dedupe de concluídas. Sem isso, item 5 pode bloquear reexecução legítima após alteração de cadastro.

Contradição atual: `src/cadastro/domain/subbacia.ts:149` e `src/cadastro/domain/cts.ts:85` ainda têm base literal no front, contra R1/R2.

Contradição atual: `app/infra/repositorios/cadastro_escrita.py:238` e `:261` ainda têm base literal no back, contra R1/R2.

Contradição atual: `app/api/simulacao.py:187` ainda expõe rota de cancelar, mas o próprio comentário diz que não está implementada por falta de status no banco. Para R4, manter endpoint morto é ruído; ou remove do contrato/front, ou implementa com regra explícita.
