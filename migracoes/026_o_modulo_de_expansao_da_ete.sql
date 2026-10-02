-- O MODULO DE EXPANSAO DA ETE NOVA TEM CAPACIDADE E PRECO PROPRIOS.
--
-- Pedido do cliente, trazido pelo dono do produto em 29/09/2026: na ETE NOVA
-- constroi-se uma quantidade definida de modulos iniciais e depois expande-se se
-- necessario — e os modulos iniciais tem vazao e preco especificos, diferentes dos
-- de expansao.
--
-- QUATRO DECISOES QUE ESTAS DUAS COLUNAS CARREGAM:
--
--   1. Vale SO PARA A ETE NOVA. Numa ETE que ja existe todo modulo e expansao no
--      sentido fisico, e `capacidade_por_modulo`/`capex_por_modulo` ja significam
--      ali "o modulo que eu construo" — ler as colunas novas mudaria o numero das
--      347 ETEs existentes do cadastro. O motor ignora estas duas quando a ETE nao
--      e nova (`modulo_de_expansao`, em `otimizador_capex_v62.py`).
--
--   2. A QUANTIDADE INICIAL CONTINUA EM `modulos`. A coluna nao muda de
--      significado nem ganha par: ela e, como sempre, quantos modulos a ETE nova
--      nasce tendo. O que passa da capacidade desse pacote e expansao.
--
--   3. O OPEX E O MESMO nos dois tipos, e por isso `opex_por_modulo` continua
--      sozinho. Nao ha `opex_por_modulo_expansao`.
--
--   4. VAZIA = IGUAL AO MODULO INICIAL, que e o comportamento de hoje. E o que
--      permite esta migracao subir sem mexer no numero de nenhuma das 639 ETEs do
--      cadastro: nenhuma tem as colunas preenchidas no dia em que ela roda.
--
-- E UM ZERO NAO E UMA AUSENCIA: o motor guarda NULL e 0 separados, porque preco
-- zero e uma afirmacao do cadastro ("este modulo nao custa") e cair no preco do
-- modulo inicial cobraria por ele.
ALTER TABLE input.ete_capex
    ADD COLUMN IF NOT EXISTS capacidade_por_modulo_expansao double precision,
    ADD COLUMN IF NOT EXISTS capex_por_modulo_expansao      double precision;

COMMENT ON COLUMN input.ete_capex.capacidade_por_modulo_expansao IS
    'Vazao de cada modulo de EXPANSAO da ETE nova, na mesma unidade de unidade_capacidade. Vazia = igual a capacidade_por_modulo. Ignorada quando a ETE nao e nova.';
COMMENT ON COLUMN input.ete_capex.capex_por_modulo_expansao IS
    'CAPEX de cada modulo de EXPANSAO da ETE nova. Vazia = igual a capex_por_modulo; zero significa sem custo, e nao ausente. Ignorada quando a ETE nao e nova.';
