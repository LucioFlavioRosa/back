-- A HIERARQUIA TERRITORIAL PASSA A SER regional > unidade > empresa > cidade.
--
-- Modelo de dados v8 (20260819_AEG_Modelo_de_Dados.docx, secao 1.3): as tabelas
-- `regional_superintendencia` e `superintendencia_cidade` saem, e entram quatro:
-- `regional` e `cidade` como entidades canonicas, `empresa` no lugar da
-- superintendencia, e `cidade_empresa` como o vinculo entre as duas pontas.
--
-- A TROCA E DE NOME E DE FORMA, NAO DE CONTEUDO. `regional_superintendencia` ja
-- era (id, nome, unidade_id) e vira `empresa` campo a campo. A antiga
-- `superintendencia_cidade` guardava o municipio E o vinculo na mesma linha, e
-- e ela que se parte em duas: o municipio passa a existir por si em `cidade`
-- (era so um nome repetido em cada vinculo) e o vinculo fica em `cidade_empresa`.
--
-- O BACKFILL VEM DAS PROPRIAS TABELAS ANTIGAS, e nao de uma carga nova: os dados
-- ja estao aqui (48 empresas, 141 municipios), e o Databricks so os reescreve na
-- proxima carga. Migrar sem backfill deixaria a aplicacao sem hierarquia entre a
-- migracao e a carga seguinte — e a hierarquia e o que alimenta os seletores de
-- regional/unidade e o escopo de visao do usuario.
--
-- IDEMPOTENTE, como as demais — e isso exige mais do que `IF NOT EXISTS` na
-- criacao e `ON CONFLICT DO NOTHING` no backfill: os backfills LEEM as tabelas
-- que o fim desta mesma migracao derruba, entao na segunda execucao eles
-- falhariam com "relation does not exist". Cada um vai dentro de um bloco
-- guardado por `to_regclass`, que devolve NULL para tabela ausente.

-- ---------------------------------------------------------------- regional
CREATE TABLE IF NOT EXISTS input.regional (
    regional_id   text PRIMARY KEY,
    regional_name text
);

-- A regional nunca teve tabela propria: ela vivia desnormalizada em
-- `unidade_regional (regional_id, regional_name)`. O `DISTINCT ON` escolhe um
-- nome por id — se duas unidades discordarem do nome da mesma regional, fica o
-- primeiro em ordem alfabetica, que e deterministico e nao inventa um terceiro.
INSERT INTO input.regional (regional_id, regional_name)
SELECT DISTINCT ON (regional_id) regional_id, regional_name
  FROM input.unidade_regional
 WHERE regional_id IS NOT NULL
 ORDER BY regional_id, regional_name
    ON CONFLICT (regional_id) DO NOTHING;

-- ----------------------------------------------------------------- empresa
CREATE TABLE IF NOT EXISTS input.empresa (
    emp_codigo text PRIMARY KEY,
    empresa    text,
    unidade_id text NOT NULL REFERENCES input.unidade_regional(unidade_id)
);

DO $$
BEGIN
    IF to_regclass('input.regional_superintendencia') IS NOT NULL THEN
        INSERT INTO input.empresa (emp_codigo, empresa, unidade_id)
        SELECT superintendencia_id, superintendencia_name, unidade_id
          FROM input.regional_superintendencia
            ON CONFLICT (emp_codigo) DO NOTHING;
    END IF;
END $$;

-- ------------------------------------------------------------------ cidade
CREATE TABLE IF NOT EXISTS input.cidade (
    cidade_id   text PRIMARY KEY,
    cidade_name text
);

-- Mesmo cuidado do `DISTINCT ON` da regional: o nome do municipio estava
-- repetido em cada linha de vinculo, e nada garantia que fosse igual.
DO $$
BEGIN
    IF to_regclass('input.superintendencia_cidade') IS NOT NULL THEN
        INSERT INTO input.cidade (cidade_id, cidade_name)
        SELECT DISTINCT ON (cidade_id) cidade_id, cidade_name
          FROM input.superintendencia_cidade
         ORDER BY cidade_id, cidade_name
            ON CONFLICT (cidade_id) DO NOTHING;
    END IF;
END $$;

-- ---------------------------------------------------------- cidade_empresa
CREATE TABLE IF NOT EXISTS input.cidade_empresa (
    cidade_id  text PRIMARY KEY REFERENCES input.cidade(cidade_id),
    emp_codigo text NOT NULL REFERENCES input.empresa(emp_codigo)
);

DO $$
DECLARE
    repetidas bigint;
BEGIN
    IF to_regclass('input.superintendencia_cidade') IS NULL THEN
        RETURN;
    END IF;

    -- O v8 declara `cidade_id` como CHAVE PRIMARIA: uma cidade pertence a uma
    -- empresa so. A tabela v7 nao tinha essa trava, entao um banco pode ter a
    -- mesma cidade ligada a duas superintendencias — e ai `ON CONFLICT DO
    -- NOTHING` guardaria uma e jogaria a outra fora sem dizer nada. A unidade do
    -- vinculo descartado perderia a cidade inteira do cadastro e da simulacao,
    -- e ninguem saberia por que.
    --
    -- Entao a migracao para e devolve o caso: qual cidade, e quantas. Escolher
    -- por ela qual empresa fica com a cidade nao e decisao de um script.
    SELECT COUNT(*) INTO repetidas FROM (
        SELECT cidade_id FROM input.superintendencia_cidade
         GROUP BY cidade_id HAVING COUNT(DISTINCT superintendencia_id) > 1
    ) x;
    IF repetidas > 0 THEN
        RAISE EXCEPTION
            '% cidade(s) estao ligadas a mais de uma superintendencia, e o '
            'modelo v8 admite uma empresa por cidade. Resolva o vinculo '
            'duplicado antes de migrar — manter so um deles aqui faria a outra '
            'unidade perder a cidade em silencio.', repetidas;
    END IF;

    INSERT INTO input.cidade_empresa (cidade_id, emp_codigo)
    SELECT cidade_id, superintendencia_id
      FROM input.superintendencia_cidade
        ON CONFLICT (cidade_id) DO NOTHING;
END $$;

-- ------------------------------------------- as FKs que apontavam para a antiga
--
-- `cidade_operacional` e `cidade_sistema` referenciavam `superintendencia_cidade`
-- — isto e, o municipio SO existia como linha de vinculo. Agora apontam para
-- `cidade`, que e o municipio de fato. Sem reapontar, o `DROP` abaixo falharia.
--
-- `NOT VALID` E DEPOIS `VALIDATE` em vez de criar ja validando: a criacao direta
-- varre todas as linhas segurando ACCESS EXCLUSIVE, e numa tabela grande isso e
-- o deploy inteiro parado. Separado, o `ADD ... NOT VALID` ainda toma ACCESS
-- EXCLUSIVE — mas so pelo instante do catalogo, sem varredura — e e o
-- `VALIDATE` que le as linhas, sob SHARE UPDATE EXCLUSIVE, que nao barra leitura
-- nem escrita.
--
-- Ou seja: ha uma janela curta de bloqueio, e ela nao cresce com o tamanho da
-- tabela. Quem planeja o deploy precisa saber das duas coisas.
ALTER TABLE input.cidade_operacional
    DROP CONSTRAINT IF EXISTS cidade_operacional_cidade_id_fkey;
ALTER TABLE input.cidade_operacional
    ADD CONSTRAINT cidade_operacional_cidade_id_fkey
    FOREIGN KEY (cidade_id) REFERENCES input.cidade(cidade_id) NOT VALID;
ALTER TABLE input.cidade_operacional
    VALIDATE CONSTRAINT cidade_operacional_cidade_id_fkey;

ALTER TABLE input.cidade_sistema
    DROP CONSTRAINT IF EXISTS cidade_sistema_cidade_id_fkey;
ALTER TABLE input.cidade_sistema
    ADD CONSTRAINT cidade_sistema_cidade_id_fkey
    FOREIGN KEY (cidade_id) REFERENCES input.cidade(cidade_id) NOT VALID;
ALTER TABLE input.cidade_sistema
    VALIDATE CONSTRAINT cidade_sistema_cidade_id_fkey;

-- A unidade passa a apontar para a regional canonica.
ALTER TABLE input.unidade_regional
    DROP CONSTRAINT IF EXISTS unidade_regional_regional_id_fkey;
ALTER TABLE input.unidade_regional
    ADD CONSTRAINT unidade_regional_regional_id_fkey
    FOREIGN KEY (regional_id) REFERENCES input.regional(regional_id) NOT VALID;
ALTER TABLE input.unidade_regional
    VALIDATE CONSTRAINT unidade_regional_regional_id_fkey;

-- --------------------------------------------------------------- orcamento
--
-- O orcamento passa a ser POR ANO: a chave era so `regional_id`, e vira
-- (regional_id, ano).
--
-- ELA PARA EM VEZ DE APAGAR. A primeira versao deste bloco fazia
-- `DELETE ... WHERE ano IS NULL` antes de por o NOT NULL, e isso e uma perda de
-- dados silenciosa esperando a hora: no instante em que a coluna e criada, TODA
-- linha preexistente tem `ano` nulo — num banco com orcamento carregado, o
-- DELETE esvaziaria a tabela inteira e a migracao terminaria dizendo "ok".
--
-- Nao ha valor honesto para "de que ano era esta verba", entao a decisao nao e
-- desta migracao: ela levanta, diz quantas linhas estao sem ano, e devolve o
-- caso para quem sabe responder. O banco local esta vazio, e por isso o caminho
-- feliz aqui e o mesmo de sempre.
ALTER TABLE input.orcamento ADD COLUMN IF NOT EXISTS ano integer;

DO $$
DECLARE
    sem_ano bigint;
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
         WHERE table_schema = 'input' AND table_name = 'orcamento'
           AND constraint_type = 'PRIMARY KEY'
           AND constraint_name = 'orcamento_pkey_ano'
    ) THEN
        RETURN;  -- ja migrada
    END IF;

    SELECT COUNT(*) INTO sem_ano FROM input.orcamento WHERE ano IS NULL;
    IF sem_ano > 0 THEN
        RAISE EXCEPTION
            'input.orcamento tem % linha(s) sem `ano`. Preencha o ano de cada '
            'verba antes de rodar esta migracao — ela nao tem como adivinhar a '
            'que exercicio cada linha pertence, e apaga-las perderia orcamento '
            'aprovado.', sem_ano;
    END IF;

    ALTER TABLE input.orcamento ALTER COLUMN ano SET NOT NULL;
    ALTER TABLE input.orcamento DROP CONSTRAINT IF EXISTS orcamento_pkey;
    ALTER TABLE input.orcamento ADD CONSTRAINT orcamento_pkey_ano
        PRIMARY KEY (regional_id, ano);
END $$;

-- ------------------------------------------------- indices das novas FKs
--
-- O Postgres indexa a chave primaria e NAO a estrangeira. As duas colunas
-- abaixo sao o join e o filtro do recorte por unidade (`_CIDADES_DA_UNIDADE`),
-- que quase toda consulta de cadastro carrega — sem indice, cada uma delas
-- varre `empresa` inteira para achar a unidade e `cidade_empresa` inteira para
-- ligar o municipio.
CREATE INDEX IF NOT EXISTS empresa_unidade_id_idx
    ON input.empresa (unidade_id);
CREATE INDEX IF NOT EXISTS cidade_empresa_emp_codigo_idx
    ON input.cidade_empresa (emp_codigo);

-- ANTERIOR A ESTA MUDANCA, e corrigido junto por ser o mesmo defeito: `cts` e
-- a FK de `subbacia_cts` para `cts_operacional`, e tambem estava sem indice.
CREATE INDEX IF NOT EXISTS subbacia_cts_cts_idx
    ON input.subbacia_cts (cts);

-- ----------------------------------------------------- as tabelas que saem
DROP TABLE IF EXISTS input.superintendencia_cidade;
DROP TABLE IF EXISTS input.regional_superintendencia;

-- O QUE ESTA MIGRACAO DELIBERADAMENTE NAO FAZ:
--
--   `input.override` e as colunas `atualizado_em`/`atualizado_por` nao existem
--   no `input.sql` de referencia porque sao NOSSAS (migracoes 001, 006 e 007) —
--   a trilha de edicao do cadastro. O script upstream descreve o banco que o
--   Databricks carrega, nao o que a aplicacao acrescenta.
--
--   `cidade_sistema.usa_sistema_cts` e `ete_capex.unidade_capacidade` tambem
--   estao fora do script, mas sao do MOTOR (`db.py` exige a primeira, citando
--   `ddl_input_migracao_06.sql` do repositorio dele) e seguem em uso — a
--   primeira governa o limite de CTS por sistema, a segunda rotula a capacidade
--   da ETE na tela. A secao "o que mudou" do documento v8 nao cita a remocao de
--   nenhuma das duas; tira-las por ausencia no script quebraria duas telas.
