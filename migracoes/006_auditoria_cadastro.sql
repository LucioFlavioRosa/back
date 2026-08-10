-- Última alteração e autor, em cada ficha de cadastro
--
-- Isto existe para SUBSTITUIR o 409 de ficha, que sai na mesma leva. Não é um
-- acréscimo: é a troca de uma proteção por outra, e vale explicar por quê.
--
-- O 409 comparava a versão lida com a versão atual e recusava a gravação quando
-- alguém tinha salvado no meio. Protegia — e cobrava caro: quem abriu a ficha de
-- manhã e salvou à tarde perdia o trabalho para um colega que mexeu em OUTRO
-- campo da mesma ficha, porque a versão é o hash da ficha inteira. O dono do
-- produto decidiu (R6): em vez de barrar, MOSTRAR. Quem vê "última alteração:
-- ana@aegea, 10/08 14:32" sabe com quem falar; quem levava 409 só sabia que
-- tinha perdido o que digitou.
--
-- O que se perde, dito sem enfeite: duas pessoas na mesma ficha continuam
-- podendo sobrescrever uma à outra, e agora sem aviso NO MOMENTO da gravação. A
-- revisão do plano mapeou os caminhos (`PLANO-REVISAO.md`, D) — sub-bacia e CTS
-- regravam obras com DELETE+INSERT, cidade apaga e reinsere metas e fator, ETE
-- faz upsert campo a campo. O sinal passa a ser posterior e visível, em vez de
-- imediato e cego.
--
-- `timestamptz` e não `timestamp`: o serviço roda em UTC e a tela mostra no fuso
-- de quem lê. Sem fuso, "14:32" é uma pergunta sem resposta.
--
-- `text` para o autor, e não uma FK para `controle.usuario_acesso`: o autor é o
-- registro de QUEM GRAVOU, não um vínculo vivo. Se a pessoa sair da empresa e a
-- linha de acesso for removida, a trilha não pode sumir junto — é o mesmo motivo
-- pelo qual `input.override.autor` é texto (`001_override.sql`).
--
-- Sem `DEFAULT now()`: ficha que nunca foi salva pela tela tem de dizer isso, com
-- nulo. Um default carimbaria a data da MIGRAÇÃO em 4.850 sub-bacias que ninguém
-- tocou, e a tela mostraria "última alteração: 10/08" para todas elas — uma
-- informação falsa, criada por conveniência de DDL.

ALTER TABLE input.subbacia_operacional
  ADD COLUMN IF NOT EXISTS atualizado_em  timestamptz,
  ADD COLUMN IF NOT EXISTS atualizado_por text;

ALTER TABLE input.cts_operacional
  ADD COLUMN IF NOT EXISTS atualizado_em  timestamptz,
  ADD COLUMN IF NOT EXISTS atualizado_por text;

ALTER TABLE input.ete_capex
  ADD COLUMN IF NOT EXISTS atualizado_em  timestamptz,
  ADD COLUMN IF NOT EXISTS atualizado_por text;

ALTER TABLE input.cidade_operacional
  ADD COLUMN IF NOT EXISTS atualizado_em  timestamptz,
  ADD COLUMN IF NOT EXISTS atualizado_por text;

COMMENT ON COLUMN input.subbacia_operacional.atualizado_em IS
  'Quando esta ficha foi gravada pela última vez. Nulo = nunca gravada pela tela.';
COMMENT ON COLUMN input.subbacia_operacional.atualizado_por IS
  'Quem gravou. Vem do TOKEN, nunca do corpo da requisição.';
COMMENT ON COLUMN input.cts_operacional.atualizado_em IS
  'Quando esta ficha foi gravada pela última vez. Nulo = nunca gravada pela tela.';
COMMENT ON COLUMN input.cts_operacional.atualizado_por IS
  'Quem gravou. Vem do TOKEN, nunca do corpo da requisição.';
COMMENT ON COLUMN input.ete_capex.atualizado_em IS
  'Quando esta ficha foi gravada pela última vez. Nulo = nunca gravada pela tela.';
COMMENT ON COLUMN input.ete_capex.atualizado_por IS
  'Quem gravou. Vem do TOKEN, nunca do corpo da requisição.';
COMMENT ON COLUMN input.cidade_operacional.atualizado_em IS
  'Quando esta ficha foi gravada pela última vez. Nulo = nunca gravada pela tela.';
COMMENT ON COLUMN input.cidade_operacional.atualizado_por IS
  'Quem gravou. Vem do TOKEN, nunca do corpo da requisição.';
