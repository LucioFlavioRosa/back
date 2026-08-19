-- A trilha passa a aceitar `topologia` — o caminho até a ETE vira dado gravável.
--
-- POR QUE ISSO PRECISA DE MIGRAÇÃO. `input.override` tem um CHECK que lista os
-- tipos de ficha, e ele nasceu com os quatro que tinham escrita: sub-bacia, CTS,
-- ETE e cidade. A topologia não estava lá porque não havia como gravá-la — a
-- tela do Grupo 01 editava contra o `sessionStorage` do navegador e avisava, em
-- letras, que nada daquilo chegava ao cadastro.
--
-- O QUE MUDA NO MODELO. A premissa antiga era que a topologia vinha pronta do
-- Databricks, como o resto do Grupo 01. É falso, e o efeito era prático: de lá
-- vêm quais sub-bacias e qual ETE pertencem ao sistema, e todas as CTS
-- cadastradas — QUEM MONTA O SISTEMA é a Regional. Duas coisas ficavam sem dono:
--
--   `componente_sistema_id_jusante`   para onde cada componente escoa
--   a linha da CTS em `sistema_topologia`   em que sistema ela entra, ou nenhum
--
-- POR QUE A TRILHA IMPORTA MAIS AQUI QUE NAS OUTRAS FICHAS. Um preço errado sai
-- errado na conta e alguém estranha o número. Um caminho errado não aparece: o
-- motor percorre `jusante` até acabar (`caminho()`, em otimizador_capex_v62.py)
-- e, se o caminho não chega na ETE, ele simplesmente não soma as obras de
-- transporte daquele trecho. O plano fica MAIS BARATO e continua plausível. Sem
-- trilha, não há como perguntar depois quem ligou o que.
--
-- `ficha_id` guarda o COMPONENTE (a chave primária de `sistema_topologia`), e os
-- campos gravados são `sisId` e `jusante` — os mesmos nomes que a tela usa.
-- Entrar e sair do sistema aparece na trilha como qualquer outro campo: criação
-- é `valor_antigo` nulo, remoção é `valor_novo` nulo, e é assim desde a 007.

ALTER TABLE input.override DROP CONSTRAINT IF EXISTS override_tipo_check;

ALTER TABLE input.override ADD CONSTRAINT override_tipo_check
  CHECK (tipo = ANY (ARRAY['sub-bacia'::text, 'cts'::text, 'ete'::text,
                           'cidade'::text, 'topologia'::text]));

COMMENT ON COLUMN input.override.tipo IS
  'Que ficha mudou: sub-bacia, cts, ete, cidade ou topologia. Em `topologia`, `ficha_id` é o componente e os campos são `sisId` (em que sistema ele está) e `jusante` (para onde escoa).';
