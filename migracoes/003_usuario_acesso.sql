-- Acesso por usuário: papel + escopo (regional / unidade)
--
-- Substitui `controle.usuario_papel`, que só carregava o papel. O que o produto
-- precisa é mais que isso: cada pessoa acessa um conjunto de regionais e
-- unidades, e o papel diz o que ela pode fazer dentro dele.
--
-- UMA LINHA = UMA CONCESSÃO. Ler assim:
--
--   login          papel     regional_id  unidade_id
--   ana@aegea      analista  rA           NULL         -> a regional rA inteira
--   bruno@aegea    analista  NULL         uB2          -> só a unidade uB2
--   bruno@aegea    analista  NULL         uB1          -> e também a uB1
--   carla@aegea    admin     NULL         NULL         -> tudo
--
-- `regional_id` e `unidade_id` ambos nulos significam ESCOPO TOTAL, e só fazem
-- sentido com um papel que justifique isso. O serviço trata `admin` assim hoje.
--
-- SEM LINHA = SEM ACESSO. É o default seguro, e tem uma consequência que precisa
-- ser dita: ao aplicar esta migração num ambiente existente, **ninguém vê nada**
-- até as concessões serem inseridas. O contrário (sem linha = vê tudo) faria a
-- autorização falhar aberta, que é o modo de falha errado para isto.
--
-- `papel` continua TEXTO LIVRE, sem CHECK: o conjunto de papéis não está fechado.
-- O código entende os que precisa e ignora o resto, então a tabela pode crescer
-- antes do código — a ordem que funciona enquanto o desenho está em aberto.
--
-- Quando o Entra ID entrar, o claim `roles` do token vira a fonte do PAPEL. O
-- ESCOPO continua aqui: quais unidades alguém acessa é decisão do negócio, não
-- do diretório corporativo.

DROP TABLE IF EXISTS controle.usuario_papel;

CREATE TABLE IF NOT EXISTS controle.usuario_acesso (
  login         text        NOT NULL,
  papel         text        NOT NULL,
  regional_id   text,
  unidade_id    text,
  concedido_em  timestamptz NOT NULL DEFAULT now(),
  concedido_por text,

  -- Uma concessão é por regional OU por unidade OU total — nunca as duas juntas,
  -- que seria ambíguo (vale a regional? só a unidade?).
  CONSTRAINT usuario_acesso_um_escopo
    CHECK (regional_id IS NULL OR unidade_id IS NULL)
);

-- `coalesce` no índice porque NULL não colide com NULL em UNIQUE: sem isto, a
-- mesma concessão total poderia ser inserida infinitas vezes.
CREATE UNIQUE INDEX IF NOT EXISTS usuario_acesso_unica
  ON controle.usuario_acesso
     (lower(login), papel, coalesce(regional_id, ''), coalesce(unidade_id, ''));

CREATE INDEX IF NOT EXISTS usuario_acesso_login
  ON controle.usuario_acesso (lower(login));

COMMENT ON TABLE controle.usuario_acesso IS
  'Quem acessa o quê. Uma linha por concessão; sem linha = sem acesso. Papel desconhecido pelo código é guardado e ignorado.';
