-- A trilha deixa de cobrir só o Databricks e passa a cobrir a ficha inteira
--
-- ## O que ela era, e por que não bastava
--
-- `input.override` nasceu para uma pergunta estreita: "que número do Databricks a
-- Regional sobrescreveu?". O próprio comentário da 001 diz, com todas as letras,
-- que ela **não é histórico de edição** e que campo preenchido pela Regional não
-- gera linha.
--
-- A consequência, medida: das quatro partes de uma ficha, só uma tinha rastro.
--
--   bloco `db` (Databricks)     tinha trilha   quem, o quê, quando
--   bloco `params`              NÃO tinha      nem o quê, nem quando
--   obras                       NÃO tinha      idem
--   cidade / metas / fator      NÃO tinha      idem
--   ETE                         NÃO tinha      idem
--
-- E havia um segundo buraco, mais discreto: a trilha era montada pelo CLIENTE e
-- enviada no corpo do `PUT`. Auditoria que depende do cliente dizer o que mudou
-- confia em quem está sendo auditado — um bug no front, e o rastro some sem
-- ninguém notar. A partir desta migração quem calcula a diferença é o SERVIDOR,
-- que tem as duas pontas: o que estava gravado e o que chegou.
--
-- ## `origem`
--
-- Distingue corrigir um número do Databricks de preencher um campo que é da
-- Regional. Na tela isso vira verbo diferente — "corrigiu" contra "alterou" —, e
-- na auditoria é a diferença entre discordar da fonte e fazer o próprio trabalho.
--
-- É derivável do nome do campo hoje (`cadastro._DO_DATABRICKS`), e mesmo assim
-- fica gravada: o conjunto do que vem do Databricks muda com o tempo, e uma
-- trilha cuja leitura muda retroativamente não é trilha. O que foi correção em
-- 2026 tem de continuar sendo correção quando alguém consultar em 2028.
--
-- O default é `databricks` porque é o que TODAS as linhas existentes são — a
-- tabela só recebia isso até aqui.
--
-- ## `valor_novo` passa a aceitar NULL
--
-- Era `NOT NULL`, e fazia sentido enquanto toda linha era "virou este valor".
-- A ficha tem coleções — metas de cobertura, faixas de paridade — em que a
-- mudança pode ser a linha DEIXAR DE EXISTIR. Sem NULL, remover uma meta seria
-- registrado como "virou vazio", indistinguível de alguém ter apagado o número e
-- deixado a meta lá.
--
-- A convenção fica simétrica e sem sentinela inventada:
--
--   valor_antigo NULL   não existia antes  (foi criado)
--   valor_novo   NULL   deixou de existir  (foi removido)
--
-- ## O que NÃO muda
--
-- Continua APPEND-ONLY, e continua gravada na MESMA transação do dado. As duas
-- regras são o que a 001 protegeu depois de uma revisão mostrar correção de julho
-- reaparecendo com data de agosto.

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
