-- A RECEITA DA SUB-BACIA TAMBEM TEM VERSAO "COM A CTS A PARTE".
--
-- A exportacao do Databricks (PORTFOLIO_INVEST_CAPEX_SUBBACIAS) traz cada medida
-- da sub-bacia em duas versoes, e a semantica — conferida em 09/2026 contra a
-- planilha de CTS, cidade a cidade — e esta:
--
--   sem sufixo   a sub-bacia INTEIRA, sem considerar a CTS: a area que o coletor
--                atende esta dentro. E o numero maior (ou igual, onde nao ha CTS).
--   `_COM_CTS`   a sub-bacia COM A CTS CONSIDERADA A PARTE: so o que nao e area
--                do coletor. Vazia quando a CTS levou a sub-bacia inteira.
--
-- As oito quantidades ja tinham a coluna `*_com_cts` (ligacoes e economias,
-- totais e residenciais). A receita nao tinha — a carga descartava
-- `MED_12M_FAT_DIR_AGUA_LIQ_COM_CTS` e `MED_12M_ARREC_DIR_AGUA_COM_CTS` —, e o
-- motor, com a CTS ligada, usava a receita inteira da sub-bacia ao lado da
-- receita da propria CTS: a area do coletor faturava DUAS VEZES. Com as duas
-- colunas, o motor le a receita `_com_cts` junto das quantidades `_com_cts`
-- quando a CTS entra como no, e a sem sufixo quando nao entra.
--
-- Nulas na base que ja existe: a carga do Databricks e quem as preenche
-- (`dev/carregar_portfolio.py`). Na base mockada, `dev/mock_com_cts_semantica.sql`
-- as deriva da propria base.
ALTER TABLE input.subbacia_operacional
    ADD COLUMN IF NOT EXISTS receita_faturada_media_mensal_com_cts   double precision,
    ADD COLUMN IF NOT EXISTS receita_arrecadada_media_mensal_com_cts double precision;

COMMENT ON COLUMN input.subbacia_operacional.receita_faturada_media_mensal_com_cts IS
    'Receita faturada media mensal da sub-bacia COM a CTS considerada a parte (so o que nao e area do coletor). O motor a le com usar_cts=True.';
COMMENT ON COLUMN input.subbacia_operacional.receita_arrecadada_media_mensal_com_cts IS
    'Receita arrecadada media mensal da sub-bacia COM a CTS considerada a parte (so o que nao e area do coletor). O motor a le com usar_cts=True.';
