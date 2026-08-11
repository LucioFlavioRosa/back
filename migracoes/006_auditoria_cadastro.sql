-- Última alteração e autor, em cada ficha de cadastro
--
-- As quatro tabelas de ficha passam a registrar QUEM gravou e QUANDO. A tela
-- mostra isso no cabeçalho da ficha (R6), e é o único sinal que o produto dá
-- sobre gravação concorrente: a escrita não tem controle otimista, então duas
-- pessoas na mesma ficha podem se sobrescrever. O sinal é posterior e legível,
-- em vez de imediato e cego.
--
-- Os caminhos de sobrescrita são conhecidos: sub-bacia e CTS regravam obras com
-- `DELETE`+`INSERT`, cidade apaga e reinsere metas e fator, ETE faz upsert campo
-- a campo.
--
-- `timestamptz` e não `timestamp`: o serviço roda em UTC e a tela mostra no fuso
-- de quem lê. Sem fuso, "14:32" é uma pergunta sem resposta.
--
-- `text` para o autor, e não FK para `controle.usuario_acesso`: o autor é o
-- registro de QUEM GRAVOU, não um vínculo vivo. Se a pessoa sair da empresa e a
-- linha de acesso for removida, a trilha não pode sumir junto — mesmo motivo de
-- `input.override.autor` ser texto.
--
-- Sem `DEFAULT now()`: ficha que nunca foi salva pela tela tem de dizer isso, com
-- nulo. Um default carimbaria a data da migração em 4.850 sub-bacias que ninguém
-- tocou, e a tela mostraria uma alteração que não houve.

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
