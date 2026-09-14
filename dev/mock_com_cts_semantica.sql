-- =====================================================================
-- A BASE MOCKADA PASSA A FALAR A LINGUA DA PLANILHA REAL NAS COLUNAS `_com_cts`.
--
-- A exportacao do Databricks traz cada medida da sub-bacia em duas versoes, e a
-- semantica (conferida em 09/2026 contra a planilha de CTS) e:
--
--   sem sufixo   a sub-bacia INTEIRA, sem considerar a CTS — a area do coletor
--                esta dentro. O numero MAIOR (ou igual, onde nao ha CTS).
--   `_com_cts`   a sub-bacia COM A CTS A PARTE: so o que nao e area do coletor.
--
-- O mock foi montado ao contrario: a sem sufixo era a parte exclusiva, e a
-- `_com_cts` = exclusiva + a linha inteira da CTS pareada (nas 337 sub-bacias
-- com par, `com_cts - sem sufixo = a coluna da CTS`, em todas as oito
-- quantidades). O motor foi virado para a semantica real; este script vira o
-- mock junto, senao a rodada com CTS ligada leria a soma e contaria o coletor
-- duas vezes.
--
-- O QUE FAZ, numa transacao:
--   1. troca as oito quantidades: sem sufixo <-> `_com_cts` (linha sem par nao
--      muda: as duas sao iguais);
--   2. receita: `_com_cts` recebe a receita de hoje (a exclusiva), e a sem
--      sufixo passa a ser exclusiva + a da CTS pareada — a sub-bacia inteira;
--   3. `*_novas_obras` = universo - atuais das colunas sem sufixo, que e como o
--      motor as deriva (a coluna no banco e so conferencia) e como a carga real
--      as grava. Este passo e idempotente por si e roda sempre.
--
-- ## Por que isto e script, e nao migracao
--
-- E DADO do mock, nao estrutura: a base real chega certa da carga
-- (`carregar_portfolio.py`) e nao precisa disto. `migracoes/` e conferido por
-- estrutura em `app/infra/db.py`; aqui nao ha o que procurar para dizer
-- "aplicada".
--
-- ## Idempotente por assinatura
--
-- Trocar duas vezes desfaz a troca. O bloco so roda se a base ainda tem a
-- assinatura antiga — alguma sub-bacia pareada com `_com_cts` MAIOR que a sem
-- sufixo —, e na semantica real isso nunca acontece. Rodar de novo nao faz nada.
--
-- ## O que NAO toca
--
-- `public.otim_*`: resultados de rodadas passadas, carimbados com o `banco_md5`
-- da base daquele momento. As proximas rodadas nascem da base virada.
-- =====================================================================
BEGIN;

DO $$
DECLARE
    antiga integer;
BEGIN
    SELECT count(*) INTO antiga
      FROM input.subbacia_operacional s
      JOIN input.subbacia_cts p ON p.sub_bacia = s.sub_bacia
     WHERE s.universo_ligacoes_com_cts > s.universo_ligacoes;

    IF antiga = 0 THEN
        RAISE NOTICE 'mock_com_cts_semantica: base ja esta na semantica real; nada a fazer';
        RETURN;
    END IF;
    RAISE NOTICE 'mock_com_cts_semantica: % sub-bacia(s) pareada(s) na semantica antiga; virando', antiga;

    -- 1. as oito quantidades trocam de lugar (a atribuicao le os valores ANTES do UPDATE)
    UPDATE input.subbacia_operacional SET
        universo_ligacoes                       = universo_ligacoes_com_cts,
        universo_ligacoes_com_cts               = universo_ligacoes,
        ligacoes_atuais                         = ligacoes_atuais_com_cts,
        ligacoes_atuais_com_cts                 = ligacoes_atuais,
        universo_economias                      = universo_economias_com_cts,
        universo_economias_com_cts              = universo_economias,
        economias_atuais                        = economias_atuais_com_cts,
        economias_atuais_com_cts                = economias_atuais,
        universo_ligacoes_residencial           = universo_ligacoes_residencial_com_cts,
        universo_ligacoes_residencial_com_cts   = universo_ligacoes_residencial,
        ligacoes_atuais_residencial             = ligacoes_atuais_residencial_com_cts,
        ligacoes_atuais_residencial_com_cts     = ligacoes_atuais_residencial,
        universo_economias_residencial          = universo_economias_residencial_com_cts,
        universo_economias_residencial_com_cts  = universo_economias_residencial,
        economias_atuais_residencial            = economias_atuais_residencial_com_cts,
        economias_atuais_residencial_com_cts    = economias_atuais_residencial
     WHERE universo_ligacoes_com_cts IS NOT NULL;

    -- 2. receita: a de hoje e a exclusiva -> vira a `_com_cts`; a sem sufixo
    --    ganha a da CTS pareada, como as quantidades ja tinham.
    UPDATE input.subbacia_operacional s SET
        receita_faturada_media_mensal_com_cts   = s.receita_faturada_media_mensal,
        receita_arrecadada_media_mensal_com_cts = s.receita_arrecadada_media_mensal,
        receita_faturada_media_mensal   = s.receita_faturada_media_mensal
                                          + coalesce(c.receita_faturada_media_mensal, 0),
        receita_arrecadada_media_mensal = s.receita_arrecadada_media_mensal
                                          + coalesce(c.receita_arrecadada_media_mensal, 0)
      FROM input.subbacia_cts p
      JOIN input.cts_operacional c ON c.cts = p.cts
     WHERE p.sub_bacia = s.sub_bacia;

    UPDATE input.subbacia_operacional s SET
        receita_faturada_media_mensal_com_cts   = s.receita_faturada_media_mensal,
        receita_arrecadada_media_mensal_com_cts = s.receita_arrecadada_media_mensal
     WHERE s.receita_faturada_media_mensal_com_cts IS NULL
       AND NOT EXISTS (SELECT 1 FROM input.subbacia_cts p WHERE p.sub_bacia = s.sub_bacia);
END $$;

-- 3. as novas acompanham a sub-bacia inteira (universo - atuais), como o motor deriva
UPDATE input.subbacia_operacional SET
    ligacoes_novas_obras  = greatest(0, universo_ligacoes  - ligacoes_atuais),
    economias_novas_obras = greatest(0, universo_economias - economias_atuais)
 WHERE universo_ligacoes IS NOT NULL AND ligacoes_atuais IS NOT NULL
   AND (ligacoes_novas_obras  IS DISTINCT FROM greatest(0, universo_ligacoes  - ligacoes_atuais)
     OR economias_novas_obras IS DISTINCT FROM greatest(0, universo_economias - economias_atuais));

COMMIT;
