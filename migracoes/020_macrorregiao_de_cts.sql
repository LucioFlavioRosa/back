-- A MACRORREGIAO DE CTS GANHA A COLUNA QUE A IDENTIFICA.
--
-- `unidade_regional.usa_macrorregiao_cts` (migracao 016) diz que a unidade
-- trabalha em macrorregiao: um coletor atende a regiao, e cada sistema comporta
-- UMA CTS. O que faltava era dizer QUAIS CTS formam cada macrorregiao — a chave
-- existia na origem e nunca foi trazida para ca.
--
-- `sistema_cts` e ATRIBUTO DA CTS, vindo do Databricks. NAO se deduz do
-- `sistema_topologia`: aquele `sistema_id` e o sistema de ESGOTO, o mesmo das
-- sub-bacias e da ETE (no dump de 03/09, `e2s64` tem 10 sub-bacias, 1 ETE e 2
-- CTS). Agrupar por ele juntaria as CTS que ja estao no mesmo sistema, que e
-- outra pergunta — e deixaria de fora as 151 CTS ainda nao colocadas, que sao
-- justamente as que se quer oferecer para montar o sistema.
--
-- O NOME E O DA ORIGEM. `sistema_cts` e como o Databricks chama a coluna e como
-- quem opera se refere a ela; manter o nome poupa um de/para a cada conferencia
-- contra a base.
--
-- O SUFIXO `_cts` NAO E DECORACAO. Em `input`, `sistema` sozinho significa o
-- conjunto de sub-bacias que escoam para a mesma ETE — outra coisa, e a colisao
-- ja custou uma renomeacao antes: a caixa da tela deixou de ser "usa sistema de
-- CTS" e virou "macrorregiao" em 09/2026 justamente por dizer "sistema" com dois
-- sentidos na mesma frase. Aqui os dois convivem porque o sufixo os separa.
--
-- NULA E O ESTADO NORMAL de quem ainda nao recebeu carga com a coluna, e de
-- unidade que nao trabalha em macrorregiao. Nao ha default: inventar um valor
-- criaria macrorregiao onde a origem nao declarou nenhuma.
ALTER TABLE input.cts_operacional
  ADD COLUMN IF NOT EXISTS sistema_cts text;

COMMENT ON COLUMN input.cts_operacional.sistema_cts IS
  'A macrorregiao a que esta CTS pertence, vinda do Databricks (la se chama '
  '"sistema CTS"). Com `unidade_regional.usa_macrorregiao_cts` marcada, as CTS '
  'com o mesmo par (sistema_cts, emp_codigo) sao lidas como uma so.';

-- O agrupamento e por (sistema_cts, emp_codigo), e a empresa chega pela
-- cidade. Este indice serve as duas leituras que a tela faz: as macrorregioes da
-- unidade, e os membros de uma delas.
CREATE INDEX IF NOT EXISTS ix_cts_sistema_cts
  ON input.cts_operacional (sistema_cts)
  WHERE sistema_cts IS NOT NULL;
