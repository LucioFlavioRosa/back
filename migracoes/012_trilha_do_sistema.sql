-- A trilha passa a aceitar `sistema` — o sistema ganha um campo de cadastro.
--
-- Até aqui o sistema não tinha o que gravar: nome vem do Databricks, e a
-- composição dele é a topologia (tipo `topologia`, migração 011). Com
-- `usa_sistema_cts` ele passa a ter um campo próprio, preenchido pela Regional,
-- e toda gravação de cadastro deixa rastro.
--
-- POR QUE O RASTRO IMPORTA AQUI. O campo não muda número nenhum — ele muda o que
-- o servidor ACEITA: marcado, o sistema recusa a segunda CTS. Quem for investigar
-- por que uma CTS não pôde ser adicionada precisa saber quem marcou, e quando.
-- Sem isso a recusa parece defeito do produto.
--
-- `ficha_id` guarda o `sistema_id`, e o campo gravado é `usaCts` — o mesmo nome
-- que a tela usa. Booleano viaja na trilha como `Sim`/`Nao`, que é o formato de
-- `_texto_trilha` desde a 007: a trilha é lida por gente.

ALTER TABLE input.override DROP CONSTRAINT IF EXISTS override_tipo_check;

ALTER TABLE input.override ADD CONSTRAINT override_tipo_check
  CHECK (tipo = ANY (ARRAY['sub-bacia'::text, 'cts'::text, 'ete'::text,
                           'cidade'::text, 'topologia'::text, 'sistema'::text]));

COMMENT ON COLUMN input.override.tipo IS
  'Que ficha mudou: sub-bacia, cts, ete, cidade, topologia ou sistema. Em `topologia`, `ficha_id` é o componente (campos `sisId` e `jusante`); em `sistema`, é o sistema (campo `usaCts`).';
