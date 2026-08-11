-- A trilha do cadastro: `origem` do campo, e remoção registrável
--
-- `input.override` guarda cada campo de ficha alterado, com valor anterior, valor
-- novo, autor e instante. É APPEND-ONLY, e é gravada na MESMA transação do dado.
-- Quem calcula a diferença é o servidor, comparando o que está gravado com o que
-- chega no `PUT` — o corpo não a envia.
--
-- ## `origem`
--
-- `databricks` é correção de um número que veio de fora; `regional` é campo que a
-- Regional preenche. A tela usa verbos diferentes para os dois, e na auditoria é
-- a diferença entre discordar da fonte e fazer o próprio trabalho.
--
-- Fica GRAVADA, e não derivada do nome do campo: o conjunto do que vem do
-- Databricks muda com o tempo, e uma trilha cuja leitura muda retroativamente não
-- é trilha. O que foi correção em 2026 continua sendo correção em 2028.
--
-- ## `valor_novo` aceita NULL
--
-- A ficha tem coleções — metas de cobertura, faixas de paridade — em que a
-- mudança pode ser a linha DEIXAR DE EXISTIR. A convenção fica simétrica:
--
--   valor_antigo NULL   não existia antes  (foi criado)
--   valor_novo   NULL   deixou de existir  (foi removido)
--
-- Sem isso, remover uma meta e apagar o número dela seriam indistinguíveis.

ALTER TABLE input.override
  ADD COLUMN IF NOT EXISTS origem text NOT NULL DEFAULT 'databricks';

ALTER TABLE input.override
  DROP CONSTRAINT IF EXISTS override_origem_conhecida;
ALTER TABLE input.override
  ADD CONSTRAINT override_origem_conhecida
    CHECK (origem IN ('databricks', 'regional'));

ALTER TABLE input.override
  ALTER COLUMN valor_novo DROP NOT NULL;

-- A consulta da tela nova é "o histórico DESTA ficha, do mais recente para o mais
-- antigo" — que os índices da 001 já servem (`ix_override_ficha`). Este acrescenta
-- o caso "o que mudou nesta unidade", que a tela de auditoria por unidade pede com
-- filtro de tipo.
CREATE INDEX IF NOT EXISTS ix_override_unidade_tipo
    ON input.override (unidade_id, tipo, gravado_em DESC);

COMMENT ON TABLE input.override IS
    'Trilha de auditoria do cadastro: cada campo alterado, com valor anterior, '
    'valor novo, autor e instante. Append-only, gravada na MESMA transacao do '
    'dado. O SERVIDOR calcula a diferenca — o corpo do PUT nao a envia.';
COMMENT ON COLUMN input.override.origem IS
    'databricks = correcao de numero vindo do Databricks; regional = campo que a '
    'Regional preenche. Gravado, e nao derivado, para a leitura nao mudar quando '
    'o conjunto de campos do Databricks mudar.';
COMMENT ON COLUMN input.override.valor_antigo IS
    'Valor anterior. NULL = o campo/registro nao existia antes.';
COMMENT ON COLUMN input.override.valor_novo IS
    'Valor novo. NULL = o campo/registro deixou de existir.';
