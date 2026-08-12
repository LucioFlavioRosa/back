-- Quais rodadas cada pessoa marcou como favorita
--
-- POR QUE UMA TABELA, E NAO UMA COLUNA NA RODADA
--
-- Favoritar e um julgamento de QUEM OLHA, nao um atributo da rodada. O modelo de
-- identidade deste servico ja separa as duas coisas (`app/api/deps.py`): ESCOPO e
-- quais unidades a pessoa acessa; POSSE e de quem e uma rodada. Uma pessoa comum
-- so ve as proprias rodadas, mas o `admin` ve as de todo mundo — e com uma coluna
-- `favorita` na rodada, o admin marcando uma estrela apareceria na tela do DONO.
--
-- Seria a mesma confusao que o resto do servico evita com cuidado: o login sai do
-- token justamente para ninguem assinar nem ler o trabalho de outro. Marcar por
-- cima seria a terceira forma disso.
--
-- POR QUE SEM CHAVE ESTRANGEIRA
--
-- Nao existe UMA tabela com todas as rodadas. A rodada nasce em
-- `controle.run_request` e migra para `public.otim_meta` ao publicar — e o pacote
-- de producao publica DIRETO, sem passar pela fila, entao ha rodada em `otim_meta`
-- que nunca teve `run_request`. Uma FK para qualquer uma das duas recusaria
-- favoritar metade dos casos.
--
-- O preco e a limpeza manual: `DELETE /runs/{id}` apaga as marcas junto
-- (`resultado.excluir`). Linha orfa aqui nao quebra nada — ela some da tela porque
-- a rodada nao esta mais na lista —, mas nao ha razao para acumular.

CREATE TABLE IF NOT EXISTS controle.run_favorita (
    run_id     text        NOT NULL,
    usuario    text        NOT NULL,
    criado_em  timestamptz NOT NULL DEFAULT now(),

    -- A chave composta e o que torna favoritar IDEMPOTENTE: `PUT` duas vezes nao
    -- cria duas linhas, e o duplo clique nao precisa de tratamento na API.
    PRIMARY KEY (run_id, usuario)
);

COMMENT ON TABLE controle.run_favorita IS
  'Marcacao de favorita, POR USUARIO. Nao e atributo da rodada: o admin ve as '
  'rodadas dos outros, e a estrela dele nao pode aparecer na tela do dono.';

-- A consulta quente e "as favoritas DESTA pessoa", para marcar a lista do
-- historico e para filtrar por elas. A PK cobre `(run_id, usuario)`; este indice
-- cobre o outro sentido, que e o que a tela pede.
CREATE INDEX IF NOT EXISTS ix_run_favorita_usuario
    ON controle.run_favorita (usuario, run_id);
