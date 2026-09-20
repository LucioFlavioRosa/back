-- O ID DA MACRORREGIÃO PASSA A SER nome|empresa|unidade.
--
-- Até 20/09/2026 a linha da macrorregião em `cts_operacional` tinha `cts` = o nome
-- (`sistema_cts`), e o nome se repete entre unidades: a segunda unidade a colocar
-- "Sarapuí" encontrava a linha da primeira. Este script reescreve as linhas já
-- existentes para o id composto (`app/dominio/macrorregiao_cts.id_da_macrorregiao`)
-- e arrasta quem aponta para elas: a topologia (o próprio componente e quem escoa
-- para ele), as obras e a trilha (`override`). A empresa e a unidade saem da
-- cidade dominante da linha, que é de um membro — e todo membro é da empresa do
-- par.
--
-- Idempotente: uma linha que já tem o separador é pulada. Roda numa transação:
-- ou migra tudo, ou nada.
--
--   docker exec -i otimizador-backend-db-1 psql -U otim -d otimizador < dev/migrar_id_da_macrorregiao.sql

BEGIN;

DO $$
DECLARE
    m record;
    novo text;
BEGIN
    FOR m IN
        SELECT o.cts, ce.emp_codigo, e.unidade_id, t.componente_sistema_nome
          FROM input.cts_operacional o
          JOIN input.cidade_empresa ce ON ce.cidade_id = o.cidade_id
          JOIN input.empresa e USING (emp_codigo)
          LEFT JOIN input.sistema_topologia t ON t.componente_sistema_id = o.cts
         WHERE o.e_macrorregiao AND position('|' IN o.cts) = 0
    LOOP
        novo := m.cts || '|' || m.emp_codigo || '|' || m.unidade_id;
        RAISE NOTICE 'macrorregião % -> %', m.cts, novo;

        -- A linha nova primeiro (a chave primária não muda no lugar com FK apontando).
        INSERT INTO input.cts_operacional
        SELECT * FROM jsonb_populate_record(
            NULL::input.cts_operacional,
            (SELECT to_jsonb(o) - 'cts' || jsonb_build_object('cts', novo)
               FROM input.cts_operacional o WHERE o.cts = m.cts));

        UPDATE input.componentes_cts_capex SET cts = novo WHERE cts = m.cts;
        UPDATE input.subbacia_cts          SET cts = novo WHERE cts = m.cts;
        UPDATE input.override              SET ficha_id = novo WHERE ficha_id = m.cts;

        -- A topologia: o componente e quem escoa para ele. O nome fica gravado, porque
        -- a tela deixa de poder lê-lo do id.
        UPDATE input.sistema_topologia
           SET componente_sistema_id_jusante = novo
         WHERE componente_sistema_id_jusante = m.cts;
        UPDATE input.sistema_topologia
           SET componente_sistema_id = novo,
               componente_sistema_nome = coalesce(componente_sistema_nome, m.cts)
         WHERE componente_sistema_id = m.cts;

        DELETE FROM input.cts_operacional WHERE cts = m.cts;
    END LOOP;
END $$;

-- Conferência: nenhuma macrorregião com o id antigo, e nenhuma referência órfã.
SELECT count(*) AS macrorregioes_com_id_antigo
  FROM input.cts_operacional WHERE e_macrorregiao AND position('|' IN cts) = 0;
SELECT count(*) AS obras_orfas
  FROM input.componentes_cts_capex c
 WHERE NOT EXISTS (SELECT 1 FROM input.cts_operacional o WHERE o.cts = c.cts);

COMMIT;
