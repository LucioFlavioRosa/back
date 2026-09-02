-- A DIRETORIA ENTRA NA HIERARQUIA, ENTRE A REGIONAL E A UNIDADE.
--
-- A hierarquia confirmada com o cliente (01/09) e:
--
--   regional -> diretoria -> unidade -> empresa -> cidade -> sistema
--
-- O banco tinha todos os niveis menos a diretoria: `unidade_regional` apontava
-- direto para `regional`. A fonte confirma a posicao — o extrato
-- `PORTFOLIO_INVEST_CAPEX_SUBBACIAS_portfolio_invest_cts` traz as colunas nesta
-- ordem: REGIONAL, DIRETORIA, UNIDADE, EMP_CODIGO, EMPRESA, CIDADE.
--
-- A TABELA ESPELHA `input.regional`, e nao e coluna solta em `unidade_regional`:
-- e um NIVEL da hierarquia, com nome proprio e mais de uma unidade abaixo. O
-- nome fica TAMBEM desnormalizado na unidade (`diretoria_name`), pela mesma
-- razao que `regional_name` ja esta la: a leitura do Grupo 01 monta a arvore
-- inteira numa consulta so, e um JOIN a mais por nivel em toda consulta de
-- cadastro paga caro por um texto que nunca muda sozinho.
--
-- `regional_id` FICA em `unidade_regional`, e isso nao e redundancia por
-- descuido. Tres coisas dependem dele hoje — `orcamento(regional_id, ano)`,
-- `regional_operacional(regional_id)` e a listagem de regionais do front —, e a
-- regional continua sendo a mesma pela diretoria ou pela unidade. Tira-lo seria
-- reescrever essas tres por uma consistencia que o proprio esquema ja mantem
-- desnormalizada (`regional_name` esta la desde sempre).
CREATE TABLE IF NOT EXISTS input.diretoria (
    diretoria_id   text PRIMARY KEY,
    diretoria_name text,
    regional_id    text NOT NULL REFERENCES input.regional(regional_id)
);

COMMENT ON TABLE input.diretoria IS
  'Nivel entre a regional e a unidade. Vem do Databricks (coluna DIRETORIA do '
  'extrato de portfolio), que nao traz id proprio — o id e derivado na carga.';

CREATE INDEX IF NOT EXISTS ix_diretoria_regional ON input.diretoria (regional_id);

-- NULAVEL, de proposito. A carga do Databricks pode trazer uma unidade antes de
-- a diretoria dela existir, e um NOT NULL faria a carga inteira falhar por um
-- nivel que ainda nao chegou. Nulo aqui significa "a diretoria ainda nao veio",
-- que e uma afirmacao verdadeira; inventar uma seria mentira gravada.
ALTER TABLE input.unidade_regional
  ADD COLUMN IF NOT EXISTS diretoria_id   text REFERENCES input.diretoria(diretoria_id),
  ADD COLUMN IF NOT EXISTS diretoria_name text;

CREATE INDEX IF NOT EXISTS ix_unidade_diretoria
  ON input.unidade_regional (diretoria_id);

-- O BACKFILL CRIA UMA DIRETORIA POR UNIDADE, COM O NOME DA UNIDADE.
--
-- Nao e invencao: e o que a fonte mostra. No extrato de portfolio as 303 linhas
-- tem DIRETORIA igual a UNIDADE ('Aguas do Rio' nas duas colunas). A diretoria
-- e um nivel que AGRUPA unidades, e quando a fonte comeca a trazer mais de uma
-- unidade por diretoria a carga reescreve estas linhas — o backfill so garante
-- que nenhuma unidade que ja existe fique orfa do nivel novo.
--
-- O ID E DERIVADO porque a fonte nao tem um: ela traz o NOME da diretoria, como
-- traz o da regional. `dir-<unidade_id>` e explicito sobre a derivacao e nao
-- colide com id nenhum que a carga possa trazer depois.
--
-- QUANDO A CARGA REAL CHEGAR, o id sai do par (regional, nome): a fonte traz
-- DIRETORIA como TEXTO, sem id, e dois nomes iguais em regionais diferentes sao
-- diretorias diferentes. A carga deve derivar `diretoria_id` de
-- `(regional_id, DIRETORIA)` — nao do nome sozinho, que colidiria entre
-- regionais, nem da unidade, que e o que este backfill usa por nao ter outra
-- coisa. `unidade_regional.diretoria_id` e entao reescrito pelo id novo, e as
-- linhas `dir-<unidade_id>` que sobrarem sem unidade nenhuma podem ser apagadas.
INSERT INTO input.diretoria (diretoria_id, diretoria_name, regional_id)
SELECT 'dir-' || u.unidade_id, u.unidade_name, u.regional_id
  FROM input.unidade_regional u
 WHERE u.regional_id IS NOT NULL
ON CONFLICT (diretoria_id) DO NOTHING;

UPDATE input.unidade_regional u
   SET diretoria_id   = 'dir-' || u.unidade_id,
       diretoria_name = u.unidade_name
 WHERE u.diretoria_id IS NULL
   AND EXISTS (SELECT 1 FROM input.diretoria d
                WHERE d.diretoria_id = 'dir-' || u.unidade_id);
