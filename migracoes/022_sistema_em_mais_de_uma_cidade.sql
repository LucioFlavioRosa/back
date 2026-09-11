-- UM SISTEMA PODE ESTAR EM MAIS DE UMA CIDADE.
--
-- A regra do cliente mudou (09/2026): um sistema de esgotamento — o conjunto de
-- sub-bacias que escoam para a mesma ETE — atravessa municipio. A exportacao de
-- 10/09 tem 12 assim: `Sarapui` em 5 cidades, `Pavuna` em 3. O que NAO mudou: um
-- sistema tem UMA ETE.
--
-- `cidade_sistema` tinha chave em `sistema_id` sozinho — uma cidade por sistema.
-- Passa a ser o PAR: uma linha por cidade que o sistema atende. Toda linha que ja
-- existe continua valida (a base mockada tem uma cidade por sistema); o que muda e
-- o que a tabela ACEITA.
--
-- COM ISSO, TODO JOIN CEGO POR `sistema_id` PASSA A MULTIPLICAR LINHA: um
-- componente de um sistema em duas cidades saia duas vezes — pendencia em dobro,
-- arvore duplicada, trilha contada duas vezes. O recorte `SISTEMAS_DA_UNIDADE`
-- (`repositorios/recortes.py`) responde "quais sistemas sao desta unidade" com
-- DISTINCT, e e por ele que as consultas passam a entrar.
-- O SISTEMA GANHA TABELA PROPRIA. `sistema_topologia.sistema_id` era FK para
-- `cidade_sistema(sistema_id)` — o que so funcionava enquanto o sistema tinha
-- uma linha. A entidade passa a morar em `input.sistema`, e `cidade_sistema`
-- vira o que o nome sempre disse: o PAR cidade-sistema.
CREATE TABLE IF NOT EXISTS input.sistema (
    sistema_id   text PRIMARY KEY,
    sistema_name text
);
INSERT INTO input.sistema (sistema_id, sistema_name)
SELECT DISTINCT ON (sistema_id) sistema_id, sistema_name
  FROM input.cidade_sistema
 ORDER BY sistema_id, sistema_name
ON CONFLICT (sistema_id) DO NOTHING;

ALTER TABLE input.sistema_topologia
  DROP CONSTRAINT IF EXISTS sistema_topologia_sistema_id_fkey;
ALTER TABLE input.sistema_topologia
  ADD CONSTRAINT sistema_topologia_sistema_id_fkey
  FOREIGN KEY (sistema_id) REFERENCES input.sistema (sistema_id);

ALTER TABLE input.cidade_sistema DROP CONSTRAINT IF EXISTS cidade_sistema_pkey;
ALTER TABLE input.cidade_sistema ADD PRIMARY KEY (sistema_id, cidade_id);
ALTER TABLE input.cidade_sistema
  DROP CONSTRAINT IF EXISTS cidade_sistema_sistema_id_fkey;
ALTER TABLE input.cidade_sistema
  ADD CONSTRAINT cidade_sistema_sistema_id_fkey
  FOREIGN KEY (sistema_id) REFERENCES input.sistema (sistema_id);

COMMENT ON TABLE input.sistema IS
  'O sistema de esgotamento: as sub-bacias que escoam para a mesma ETE. Pode '
  'estar em varias cidades (ver `cidade_sistema`); tem UMA ETE.';

COMMENT ON TABLE input.cidade_sistema IS
  'Uma linha por (sistema, cidade): as cidades que cada sistema atende. Um sistema '
  'pode estar em varias cidades (desde 09/2026); `sistema_name` se repete entre '
  'as linhas do mesmo sistema.';

-- A SUB-BACIA SABE A CIDADE DELA. Ate aqui a cidade da sub-bacia era a do
-- sistema — e com sistema em varias cidades essa deducao deixa de existir. A
-- origem traz a cidade de cada sub-bacia na mesma linha (coluna CIDADE do
-- portfolio); a CTS ja tinha a sua desde a migracao 018, pela mesma razao.
--
-- O preenchimento inicial vem do sistema: na base mockada, e a unica cidade
-- dele, entao nenhuma sub-bacia muda de lugar. Numa base carregada da origem, a
-- carga escreve a coluna direto.
ALTER TABLE input.subbacia_operacional
  ADD COLUMN IF NOT EXISTS cidade_id text REFERENCES input.cidade (cidade_id);

UPDATE input.subbacia_operacional b
   SET cidade_id = cs.cidade_id
  FROM input.sistema_topologia t
  JOIN input.cidade_sistema cs ON cs.sistema_id = t.sistema_id
 WHERE t.componente_sistema_id = b.sub_bacia
   AND b.cidade_id IS NULL;

COMMENT ON COLUMN input.subbacia_operacional.cidade_id IS
  'A cidade da sub-bacia, vinda da origem. Nao se deduz mais do sistema: ele pode '
  'estar em varias.';

CREATE INDEX IF NOT EXISTS ix_subbacia_cidade
  ON input.subbacia_operacional (cidade_id);
