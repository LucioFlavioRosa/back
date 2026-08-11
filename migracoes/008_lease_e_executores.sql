-- Quem está executando a rodada, até quando, e quem está vivo para executar
--
-- O PROBLEMA QUE ISTO RESOLVE
--
-- A rodada tinha estado (`PENDENTE`, `RODANDO`) e não tinha DONO. Consequência
-- medida três vezes num dia: executor que morre deixa a rodada em `RODANDO` para
-- sempre, e ninguém percebe — não há como distinguir "está trabalhando" de
-- "morreu no meio", porque as duas coisas se parecem no banco.
--
-- Reentrega de fila resolve o executor que MORRE (o lock expira e a mensagem
-- volta). Não resolve o que TRAVA VIVO: ele segura o lock, a mensagem não volta,
-- e a rodada fica `RODANDO` indefinidamente. Só um lease com prazo resolve isso.
--
-- POR QUE LEASE, E NÃO "sem progresso há N minutos"
--
-- Tempo parado não é evidência de morte: a materialização da maior unidade leva
-- ~9,5 minutos sem escrever nada. Um critério por silêncio mataria trabalho vivo.
--
-- O lease inverte o ônus: o executor AFIRMA, a cada batida, que continua nisso.
-- Parar de afirmar é o sinal — e é um sinal que ele emite, não que se deduz. Um
-- watchdog que respeite o lease só declara morto quem deixou de dizer que está
-- vivo.

-- ---------------------------------------------------------------- a rodada
ALTER TABLE controle.run_status
  ADD COLUMN IF NOT EXISTS worker_id text,
  ADD COLUMN IF NOT EXISTS lease_ate timestamptz;

COMMENT ON COLUMN controle.run_status.worker_id IS
  'Quem reivindicou esta rodada. NULL = ninguem a pegou ainda.';
COMMENT ON COLUMN controle.run_status.lease_ate IS
  'Ate quando a reivindicacao vale. Passou disso sem renovar, o executor morreu '
  'ou travou, e o watchdog pode declarar ERRO sem adivinhar.';

-- O watchdog varre por `lease_ate` vencido; o índice existe para essa varredura
-- não custar uma leitura da tabela inteira a cada minuto.
CREATE INDEX IF NOT EXISTS ix_run_status_lease
    ON controle.run_status (lease_ate)
 WHERE status = 'RODANDO';

-- ------------------------------------------------------------- CANCELADA
-- O CHECK aceitava PENDENTE, RODANDO, SUCESSO, FALHOU_QUALIDADE e ERRO. A tela e
-- o `CONTRATO.md` §4.3 sempre falaram de CANCELADA, e o backend respondia 501 em
-- `POST /runs/{id}/cancelar` porque o banco recusaria o valor.
--
-- Sem isto, quem dispara uma rodada não tem como desistir dela: só esperar. Numa
-- simulação que leva dez minutos, é a diferença entre uma tela que responde e uma
-- que aprisiona.
ALTER TABLE controle.run_status
  DROP CONSTRAINT IF EXISTS run_status_status_check;
ALTER TABLE controle.run_status
  ADD CONSTRAINT run_status_status_check
    CHECK (status IN ('PENDENTE', 'RODANDO', 'SUCESSO', 'FALHOU_QUALIDADE',
                      'ERRO', 'CANCELADA'));

-- --------------------------------------------------------------- os executores
-- QUEM ESTÁ VIVO PARA EXECUTAR. Sem esta tabela, "na fila" é uma frase sem
-- conteúdo: ela cobre tanto "todos os executores estão ocupados, você é o
-- terceiro" quanto "não há executor nenhum, isto nunca vai rodar" — e o usuário
-- não tem como saber em qual dos dois está.
--
-- É a exigência que o dono do produto colocou como inegociável: em produção,
-- ausência de informação clara não pode acontecer. Um job do Databricks que não
-- suba deixa a fila crescendo em silêncio, e a tela precisa dizer isso.
CREATE TABLE IF NOT EXISTS controle.executor (
    worker_id    text PRIMARY KEY,

    -- A batida. Quem não bate há mais que `_LIMITE_VISTO` está fora do ar, e a
    -- tela deixa de contá-lo como capacidade disponível.
    visto_em     timestamptz NOT NULL DEFAULT now(),

    -- Quantas rodadas este executor aceita ao mesmo tempo, e quantas estão nele
    -- agora. A diferença é a vaga livre — o número que responde "vou esperar?".
    capacidade   int  NOT NULL DEFAULT 1,
    em_execucao  int  NOT NULL DEFAULT 0,

    -- Orçamento de memória e o quanto dele está comprometido. Duas rodadas
    -- grandes juntas derrubaram o processo por falta de RAM, e limitar por
    -- QUANTIDADE não impede isso: 4 unidades pequenas cabem, 2 grandes não.
    memoria_mb   int,
    memoria_uso  int  NOT NULL DEFAULT 0,

    -- Que código ele carrega. Um executor iniciado antes de uma correção segue
    -- com o código velho em memória — foi o que publicou o rótulo errado por
    -- cima do nome digitado, e levou meia hora para ser descoberto.
    versao       text
);

COMMENT ON TABLE controle.executor IS
  'Executores vivos e a capacidade deles. Alimentada por heartbeat; quem para de '
  'bater some da conta. Serve para a tela dizer POR QUE a rodada esta esperando.';

CREATE INDEX IF NOT EXISTS ix_executor_visto ON controle.executor (visto_em DESC);
