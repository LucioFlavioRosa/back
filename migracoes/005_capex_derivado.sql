-- `capex` é DERIVADO: `quantidade × preco_unitario`
--
-- A coluna existe nas duas tabelas de componente, e é escrita pelo servidor a
-- partir dos dois fatores. Esta constraint garante que ninguém grave uma segunda
-- opinião sobre a mesma conta — nem a aplicação, nem a carga, nem SQL solto.
--
-- Quem decide isso é o MOTOR: em `otimizador_capex_v62.py:1165` ele lê a
-- decomposição e a faz prevalecer sobre a coluna, avisando quando as duas
-- discordam. Um `capex` diferente da multiplicação é um número que a simulação
-- ignora.
--
-- ## A tolerância de um centavo
--
-- A planilha guarda `capex` arredondado a duas casas, e arredondar assim erra no
-- máximo meio centavo. Um centavo cobre esse erro e não cobre mais nada: uma
-- divergência maior não é precisão, é outro valor.
--
-- ## Por que CHECK, e não `GENERATED ALWAYS`
--
-- A coluna gerada seria mais forte, mas recusa `INSERT` que mencione `capex` — e
-- o carregador de produção (`carregar_postgres.py`, no repositório do otimizador)
-- manda a coluna da planilha. O CHECK deixa a carga passar, porque o
-- arredondamento cabe na tolerância.
--
-- ## Os três `IS NULL` não são folga
--
-- Componente sem `quantidade` é pendência de cadastro (`pendencias.py:_OBRA`) e
-- trava a simulação da unidade — essa é a régua, e não esta. Cobrar aqui
-- transformaria cadastro incompleto, que a tela sabe mostrar, em erro de escrita
-- sem explicação. E `capex` nulo é a resposta certa quando falta um fator: zero
-- afirmaria "esta obra não custa nada".

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
