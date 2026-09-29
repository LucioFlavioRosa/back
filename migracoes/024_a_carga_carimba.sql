-- A CARGA DO CADASTRO PASSA A CARIMBAR QUANDO ESCREVEU.
--
-- O PROBLEMA. A deduplicacao de rodada (`controle.rodada_identica`) so devolve uma
-- rodada concluida se ela for POSTERIOR a ultima alteracao do cadastro — senao
-- afirmaria que o resultado de ontem continua valendo sobre precos e vazoes que
-- mudaram. A conta usava `atualizado_em`, e essa coluna e carimbada apenas pelo `PUT`
-- da ficha (`cadastro_escrita._marcar_autoria`).
--
-- Resultado: CARGA DA PLANILHA E SQL SOLTO NAO CARIMBAVAM NADA. Depois de uma carga,
-- pedir a mesma simulacao devolvia a rodada ANTIGA, com resultado calculado sobre os
-- dados anteriores, sem aviso nenhum — e sem saida pela tela, porque `/reexecutar`
-- recusa rodada publicada (409, de proposito: republicar apagaria resultado que
-- alguem ja consultou).
--
-- POR QUE COLUNA NOVA, e nao carimbar `atualizado_em`. As duas perguntas sao
-- diferentes e o produto usa as duas:
--
--   `atualizado_em`   QUEM SALVOU A FICHA, pela tela. O cabecalho da ficha mostra
--                     "salvo por X em Y" (R6), e nulo quer dizer "nunca foi salva" —
--                     e por isso que a 006 recusou `DEFAULT now()`. Se a carga
--                     escrevesse aqui, toda linha que ela toca passaria a dizer
--                     "salvo por carga", esquecendo quem salvou antes dela.
--   `carregado_em`    QUANDO A LINHA FOI ESCRITA, por qualquer um. E o que a
--                     deduplicacao precisa, e nao tem publico na tela.
--
-- Decisao do dono do produto em 29/09/2026, depois de ver o efeito colateral.
--
-- SEM BACKFILL, e isto e deliberado. As linhas que ja existem ficam com
-- `carregado_em` nulo, entao `max()` sobre elas nao muda nada e a deduplicacao segue
-- exatamente como hoje para o que ja esta no banco. Preencher com `now()` diria que o
-- cadastro inteiro mudou agora, e invalidaria a reutilizacao de TODAS as 127 rodadas
-- publicadas de uma vez — um efeito grande para uma migracao que nao mudou dado
-- nenhum.
--
-- O TRIGGER CARIMBA TODA ESCRITA, inclusive a do `PUT`. Redundante ali (o `PUT` ja
-- carimba `atualizado_em`) e correto: a coluna responde "quando esta linha foi
-- escrita", e a resposta nao depende de quem escreveu. E o unico jeito de alcancar a
-- carga do Databricks, que nao passa por codigo deste repositorio.

ALTER TABLE input.subbacia_operacional ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.cts_operacional      ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.ete_capex            ADD COLUMN IF NOT EXISTS carregado_em timestamptz;
ALTER TABLE input.cidade_operacional   ADD COLUMN IF NOT EXISTS carregado_em timestamptz;

COMMENT ON COLUMN input.subbacia_operacional.carregado_em IS
    'Quando a linha foi escrita, por QUALQUER caminho (carga, PUT, SQL). Alimenta a '
    'deduplicacao de rodada. Nao confundir com atualizado_em, que e a gravacao humana.';

-- `now()` e nao `clock_timestamp()`: todas as linhas de uma mesma carga ficam com o
-- instante da TRANSACAO, e nao microssegundos diferentes. A pergunta e "houve carga
-- depois daquela rodada?", e um instante por carga responde melhor que um por linha.
CREATE OR REPLACE FUNCTION input.carimbar_carga() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.carregado_em := now();
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION input.carimbar_carga() IS
    'BEFORE INSERT OR UPDATE nas fichas de cadastro: carimba carregado_em. Ver '
    'migracoes/024_a_carga_carimba.sql.';

-- `DROP` antes de `CREATE` porque o Postgres nao tem `CREATE OR REPLACE TRIGGER`
-- antes da versao 14, e a migracao tem de poder rodar duas vezes sem erro.
DROP TRIGGER IF EXISTS carimbar_carga ON input.subbacia_operacional;
CREATE TRIGGER carimbar_carga BEFORE INSERT OR UPDATE ON input.subbacia_operacional
    FOR EACH ROW EXECUTE FUNCTION input.carimbar_carga();

DROP TRIGGER IF EXISTS carimbar_carga ON input.cts_operacional;
CREATE TRIGGER carimbar_carga BEFORE INSERT OR UPDATE ON input.cts_operacional
    FOR EACH ROW EXECUTE FUNCTION input.carimbar_carga();

DROP TRIGGER IF EXISTS carimbar_carga ON input.ete_capex;
CREATE TRIGGER carimbar_carga BEFORE INSERT OR UPDATE ON input.ete_capex
    FOR EACH ROW EXECUTE FUNCTION input.carimbar_carga();

DROP TRIGGER IF EXISTS carimbar_carga ON input.cidade_operacional;
CREATE TRIGGER carimbar_carga BEFORE INSERT OR UPDATE ON input.cidade_operacional
    FOR EACH ROW EXECUTE FUNCTION input.carimbar_carga();
