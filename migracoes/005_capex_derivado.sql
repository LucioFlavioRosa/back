-- `capex` é DERIVADO — e o banco passa a recusar quem discordar
--
-- A coluna existia em estado misto, com dois escritores que não se conheciam:
--
--   carga da planilha   grava o `capex` da planilha, arredondado a 2 casas
--   PUT de ficha        recalcula `quantidade × preco_unitario` e grava inteiro
--
-- Medido antes desta migração: 24.250 componentes de sub-bacia e 1.348 de CTS,
-- NENHUM sem `quantidade` e `preco_unitario`. Sete linhas — as fichas `b1b25_1_1`
-- e `e1b25_1_1`, tocadas por `PUT` em teste — carregavam a precisão cheia
-- (`204866,2556`) onde a planilha trazia `204866,26`. Outras 205 divergiam da
-- multiplicação em exatamente R$ 0,005: é o arredondamento da origem, não
-- opinião de ninguém.
--
-- Quem decide não é este arquivo: é o MOTOR, e ele já decidiu. Em
-- `otimizador_capex_v62.py:1165` — *"CAPEX pode vir DECOMPOSTO em quantidade x
-- preco unitario; se vier, ele manda"* — e a linha 1192 loga aviso quando a
-- coluna diverge da multiplicação. O cadastro passa a dizer a mesma coisa que a
-- simulação, em vez de guardar um número que ela ignora.
--
-- Por que CHECK e não `GENERATED ALWAYS`: a coluna gerada seria mais forte, mas
-- recusa `INSERT` que mencione `capex` — e o carregador de PRODUÇÃO
-- (`carregar_postgres.py`, no repositório do otimizador) manda a coluna da
-- planilha. A coluna gerada quebraria a carga de produção a partir de um
-- repositório que não é o dono dela. O CHECK deixa a carga passar, porque o erro
-- de arredondamento cabe na tolerância, e ainda assim recusa uma segunda opinião
-- de verdade.
--
-- A TOLERÂNCIA é de um centavo, e o número não é gosto: arredondar a 2 casas
-- erra no máximo R$ 0,005. Um centavo é o dobro disso — cabe o arredondamento da
-- planilha e não cabe mais nada. Um `capex` que discorde da multiplicação por
-- mais que isso não é precisão: é outro valor.
--
-- Os três `IS NULL` não são folga. Componente sem `quantidade` é pendência
-- (`pendencias.py:_OBRA`) e trava a simulação da unidade — a régua é aquela, e
-- não esta. Cobrar aqui transformaria cadastro incompleto, que a tela sabe
-- mostrar, em erro de escrita sem explicação. E `capex` nulo é a resposta certa
-- quando falta um dos fatores: zero afirmaria "esta obra não custa nada".

ALTER TABLE input.componentes_subbacias_capex
  DROP CONSTRAINT IF EXISTS capex_e_derivado;
ALTER TABLE input.componentes_subbacias_capex
  ADD CONSTRAINT capex_e_derivado CHECK (
    capex IS NULL
    OR quantidade IS NULL
    OR preco_unitario IS NULL
    OR abs(capex - quantidade * preco_unitario) <= 0.01
  );

ALTER TABLE input.componentes_cts_capex
  DROP CONSTRAINT IF EXISTS capex_e_derivado;
ALTER TABLE input.componentes_cts_capex
  ADD CONSTRAINT capex_e_derivado CHECK (
    capex IS NULL
    OR quantidade IS NULL
    OR preco_unitario IS NULL
    OR abs(capex - quantidade * preco_unitario) <= 0.01
  );

COMMENT ON COLUMN input.componentes_subbacias_capex.capex IS
  'DERIVADO de quantidade × preco_unitario. Escrito só pelo servidor; a constraint capex_e_derivado recusa divergência acima de R$ 0,01. O motor prevalece a decomposição (otimizador_capex_v62.py:1165).';
COMMENT ON COLUMN input.componentes_cts_capex.capex IS
  'DERIVADO de quantidade × preco_unitario. Escrito só pelo servidor; a constraint capex_e_derivado recusa divergência acima de R$ 0,01. O motor prevalece a decomposição (otimizador_capex_v62.py:1165).';
