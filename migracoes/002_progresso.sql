-- `progresso` em controle.run_status
--
-- O front JA tem barra de progresso e nomeia a etapa por faixa
-- (src/simulacao/domain/simulacao.ts, ETAPAS): <20 "Lendo dados", <45 "Montando o
-- modelo", <90 "Resolvendo (solver)", <100 "Materializando". Sem a coluna, o
-- endpoint devolve 0 sempre e a barra salta de 0 a 100 — a tela promete um
-- acompanhamento que nao existe.
--
-- Esta e uma das migracoes que o JOB deve (ver README do backend). Aplicada aqui
-- para o ambiente local exercitar o caminho de verdade; quando entrar no
-- `ddl_otimizador.sql` do repositorio de producao, este arquivo sai.
--
-- `DEFAULT 0` e `NULL`-tolerante de proposito: o endpoint ja faz
-- `linha.get("progresso") or 0`, entao job antigo que nao escreve a coluna
-- continua funcionando.

ALTER TABLE controle.run_status
  ADD COLUMN IF NOT EXISTS progresso smallint NOT NULL DEFAULT 0
  CONSTRAINT run_status_progresso_faixa CHECK (progresso BETWEEN 0 AND 100);
