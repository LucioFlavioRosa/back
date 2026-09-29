-- O CARIMBO ALCANÇA AS OUTRAS TREZE TABELAS QUE O MOTOR LÊ.
--
-- A 024 pôs `carregado_em` nas quatro FICHAS de cadastro. O motor lê DEZESSETE tabelas
-- de `input.*` (`pacote-motor-main/carregar_postgres.py`, `ABAS`), e a deduplicação de
-- rodada olhava só aquelas quatro.
--
-- Consequência, que é exatamente o que o dono do produto quer impedir: uma escrita que
-- toque SÓ uma das outras treze não move a data, a dedupe barra o pedido repetido, e a
-- entrada era DIFERENTE. O usuário recebe um resultado calculado sobre outros dados, sem
-- aviso — e sem saída pela tela, porque `/reexecutar` recusa rodada publicada.
--
-- As que mais importam, e nenhuma delas é exótica:
--
--   componentes_subbacias_capex   as OBRAS: capex, quantidade, preço unitário, prazo
--   componentes_cts_capex         idem, da CTS
--   metas_cobertura               as metas que o otimizador tem de cumprir
--   fator_esgoto                  as faixas de paridade esgoto/água
--   subbacia_cts                  o par sub-bacia <-> coletor
--   orcamento                     o teto por ano, quando o pedido não o carrega
--   regional_operacional          o ano-base da regional
--
-- Pela TELA o buraco não aparecia: cada `PUT` grava a ficha junto (é ele que regrava as
-- obras com DELETE+INSERT, e as metas e o fator da cidade), então `atualizado_em` da
-- ficha se movia. O buraco é para carga e SQL solto, que é justamente o caso da 024.
--
-- SEM BACKFILL, pela mesma razão da 024: nulo nas linhas que já existem, e `GREATEST`
-- ignora nulo. A migração não inventa que o cadastro mudou.

ALTER TABLE input.componentes_subbacias_capex ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.componentes_cts_capex       ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.metas_cobertura             ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.fator_esgoto                ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.subbacia_cts                ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.orcamento                   ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.regional_operacional        ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
-- As da hierarquia e da topologia: mudar qualquer uma pode mover cidade de unidade ou
-- sub-bacia de sistema, e portanto muda a entrada da simulação.
ALTER TABLE input.unidade_regional            ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.diretoria                   ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.empresa                     ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.cidade_empresa              ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.cidade_sistema              ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.sistema_topologia           ADD COLUMN IF NOT EXISTS carregado_em timestamptz;

-- O MESMO gatilho da 024 (`input.carimbar_carga`), nas treze. Uma função só: a regra é
-- uma, e duas cópias divergiriam em silêncio.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'componentes_subbacias_capex', 'componentes_cts_capex', 'metas_cobertura',
        'fator_esgoto', 'subbacia_cts', 'orcamento', 'regional_operacional',
        'unidade_regional', 'diretoria', 'empresa', 'cidade_empresa', 'cidade_sistema',
        'sistema_topologia'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS carimbar_carga ON input.%I', t);
        EXECUTE format(
            'CREATE TRIGGER carimbar_carga BEFORE INSERT OR UPDATE ON input.%I'
            ' FOR EACH ROW EXECUTE FUNCTION input.carimbar_carga()', t);
    END LOOP;
END $$;
