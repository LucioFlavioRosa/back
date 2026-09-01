-- O FIM DA CONCESSAO PASSA A SER DEFINIDO POR EMPRESA.
--
-- Ate aqui ele vivia so em `cidade_operacional.data_fim_concessao`, uma linha
-- por municipio. A decisao de negocio (31/08) e que a concessao e da EMPRESA:
-- e ela que assina o contrato, e os municipios que ela atende herdam o prazo.
--
-- MAS A COLUNA DA CIDADE NAO SAI, e isso nao e meio-termo — e o que faz a
-- mudanca ser possivel sem tocar no motor. `otimizador_capex_v62.py:975` monta
-- `fim_cid` lendo `cidade_operacional` municipio a municipio; se a coluna
-- sumisse, a otimizacao pararia de saber ate quando cada cidade gera receita, e
-- o pacote do motor nao e versionado por nos.
--
-- Entao: a empresa DEFINE e a cidade RECEBE. O valor desce por gatilho, e a
-- coluna da cidade continua sendo o que o motor le.
--
-- POR QUE GATILHO E NAO CODIGO DA APLICACAO: quem escreve em `input` nao e so a
-- aplicacao — a carga do Databricks tambem escreve, e por fora dela. Propagar no
-- servico deixaria a cidade com o prazo antigo sempre que a empresa chegasse por
-- carga, e o erro so apareceria como um VPL diferente do esperado, sem nada
-- acusando.
--
-- A COLUNA NASCE VAZIA, de proposito. Hoje 37 das 39 empresas com dado tem anos
-- DIFERENTES entre suas cidades (uma delas: 2040, 2045, 2046 e 2049), e nao ha
-- resposta automatica para qual deles vale para a empresa — escolher o maior
-- estenderia concessao e inflaria receita; escolher o menor apagaria receita
-- real. Enquanto a empresa estiver vazia, cada cidade mantem o ano que ja tem, e
-- nada muda de comportamento. A escolha e da Aegea, empresa a empresa.

ALTER TABLE input.empresa
    ADD COLUMN IF NOT EXISTS data_fim_concessao integer;

COMMENT ON COLUMN input.empresa.data_fim_concessao IS
    'Ano-calendario do fim da concessao da empresa. Fonte de verdade: quando '
    'preenchido, desce para todas as cidades dela (gatilho '
    '`empresa_propaga_concessao`). Vazio = cada cidade mantem o seu.';

-- ------------------------------------------------------------- propagacao
CREATE OR REPLACE FUNCTION input.propagar_fim_concessao()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- Empresa sem prazo nao apaga o das cidades: "ainda nao informado" e
    -- diferente de "nao ha concessao", e zerar 141 municipios porque um campo
    -- ficou em branco seria a segunda leitura, que nao e a pretendida.
    IF NEW.data_fim_concessao IS NULL THEN
        RETURN NEW;
    END IF;

    UPDATE input.cidade_operacional o
       SET data_fim_concessao = NEW.data_fim_concessao
      FROM input.cidade_empresa ce
     WHERE ce.cidade_id = o.cidade_id
       AND ce.emp_codigo = NEW.emp_codigo
       -- `IS DISTINCT FROM` e nao `<>`: com `<>`, a cidade de valor NULL nunca
       -- casaria e ficaria para tras justamente no caso que a propagacao existe
       -- para resolver. E evita reescrever linha que ja esta certa.
       AND o.data_fim_concessao IS DISTINCT FROM NEW.data_fim_concessao;

    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS empresa_propaga_concessao ON input.empresa;
CREATE TRIGGER empresa_propaga_concessao
    AFTER INSERT OR UPDATE OF data_fim_concessao ON input.empresa
    FOR EACH ROW
    EXECUTE FUNCTION input.propagar_fim_concessao();

-- Uma cidade que MUDA de empresa tambem precisa herdar o prazo da nova. Sem
-- isto ela carregaria o prazo da empresa anterior ate alguem reeditar a empresa
-- nova — um vinculo corrigido no cadastro deixaria a receita errada.
CREATE OR REPLACE FUNCTION input.herdar_fim_concessao()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    prazo integer;
BEGIN
    SELECT e.data_fim_concessao INTO prazo
      FROM input.empresa e WHERE e.emp_codigo = NEW.emp_codigo;

    IF prazo IS NOT NULL THEN
        UPDATE input.cidade_operacional
           SET data_fim_concessao = prazo
         WHERE cidade_id = NEW.cidade_id
           AND data_fim_concessao IS DISTINCT FROM prazo;
    END IF;

    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS cidade_herda_concessao ON input.cidade_empresa;
CREATE TRIGGER cidade_herda_concessao
    AFTER INSERT OR UPDATE OF emp_codigo ON input.cidade_empresa
    FOR EACH ROW
    EXECUTE FUNCTION input.herdar_fim_concessao();

-- ------------------------------------------- a trilha aceita a ficha de empresa
--
-- `input.override` guarda quem mudou o que no cadastro, e `tipo` tem um CHECK
-- com a lista de fichas editaveis. A empresa passou a ser uma delas (o fim da
-- concessao se informa nela), e sem esta linha a gravacao falha com
-- `override_tipo_check` DEPOIS de o UPDATE ja ter passado — a transacao volta
-- atras, mas o erro chega como 500 sem dizer o que houve.
ALTER TABLE input.override DROP CONSTRAINT IF EXISTS override_tipo_check;
ALTER TABLE input.override ADD CONSTRAINT override_tipo_check
  CHECK (tipo = ANY (ARRAY['sub-bacia'::text, 'cts'::text, 'ete'::text,
                           'cidade'::text, 'topologia'::text, 'sistema'::text,
                           'empresa'::text]));

-- ------------------------------------------ a cidade NASCE com o prazo da dona
--
-- Os dois gatilhos acima so fazem UPDATE, e uma cidade sem linha em
-- `cidade_operacional` nao tem o que atualizar: a linha so aparece quando alguem
-- salva a ficha do municipio pela primeira vez, e nesse momento o `INSERT` de
-- `salvar_contrato` traz `data_fim_concessao` NULL — a aplicacao nao manda mais
-- esse campo, de proposito.
--
-- Sem este gatilho, a cidade cadastrada DEPOIS de a empresa receber o prazo
-- nasce sem prazo nenhum, e o motor le NULL onde as irmas dela leem 2045. O erro
-- aparece como receita faltando numa cidade so.
--
-- BEFORE INSERT, e nao AFTER: preenche a propria linha que esta entrando, sem
-- um segundo UPDATE e sem reentrar no gatilho. Se a cidade ja vier com ano (a
-- carga do Databricks manda), o valor dela e respeitado — quem chega com dado
-- nao e sobrescrito por dedução.
CREATE OR REPLACE FUNCTION input.nascer_com_fim_concessao()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.data_fim_concessao IS NULL THEN
        SELECT e.data_fim_concessao INTO NEW.data_fim_concessao
          FROM input.cidade_empresa ce
          JOIN input.empresa e ON e.emp_codigo = ce.emp_codigo
         WHERE ce.cidade_id = NEW.cidade_id;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS cidade_nasce_com_concessao ON input.cidade_operacional;
CREATE TRIGGER cidade_nasce_com_concessao
    BEFORE INSERT ON input.cidade_operacional
    FOR EACH ROW
    EXECUTE FUNCTION input.nascer_com_fim_concessao();
