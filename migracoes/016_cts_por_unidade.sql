-- QUEM DECIDE SE USA MACRORREGIAO DE CTS PASSA A SER A UNIDADE, E NAO O SISTEMA.
--
-- Ate aqui a decisao vivia em `cidade_sistema.usa_sistema_cts`, uma linha por
-- sistema: cada sistema declarava, sozinho, se aceitava UMA CTS ou varias. A
-- decisao de negocio (01/09) e que isso e politica da UNIDADE — quem opera
-- decide de uma vez, e todos os sistemas da unidade seguem.
--
-- E O NOME MUDA JUNTO: `usa_macrorregiao_cts`. O arranjo que a caixa descreve e
-- uma MACRORREGIAO DE CTS — um coletor de tempo seco atendendo a regiao inteira
-- —, e nao um "sistema de CTS". O nome antigo ainda colidia com `sistema`, que
-- neste banco e outra coisa (o conjunto de sub-bacias que escoam para a mesma
-- ETE): a coluna dizia "sistema" duas vezes com dois sentidos.
--
-- A COLUNA DO SISTEMA SAI, e aqui ela pode sair mesmo: ao contrario do fim da
-- concessao (migracao 015), o MOTOR NUNCA LEU esta coluna. Para ele uma ou duas
-- CTS sao nos como quaisquer outros, e o que liga o modelo de CTS na simulacao e
-- o parametro de rodada `USAR_CTS`, que nao tem relacao com isto. A unica coisa
-- que a coluna faz e o cadastro recusar a segunda CTS num sistema marcado.
--
-- O VALOR NASCE `false` EM TODAS AS UNIDADES, e nao derivado do que os sistemas
-- diziam. Duas razoes, e a segunda e a que decide:
--
--   1. O dado nao existe para ser preservado. De 997 sistemas, UM estava marcado
--      (na uB2). Nao ha politica de unidade registrada em lugar nenhum — ha uma
--      caixa que quase ninguem marcou.
--
--   2. `true` seria uma afirmacao FALSA sobre o banco. Marcar significa "esta
--      unidade usa macrorregiao de CTS", e com isso "cada sistema dela tem no
--      maximo uma CTS" — e existe sistema com DUAS.
--      Herdar `true` por "algum sistema estava marcado" poria a unidade num
--      estado que ela mesma declara impossivel, e a proxima gravacao de
--      topologia seria recusada por uma escolha que ninguem fez.
--
-- `false` e o valor PERMISSIVO: nao invalida topologia nenhuma que ja exista, e
-- deixa a Regional marcar de proposito, no lugar novo, quando for o caso.
ALTER TABLE input.unidade_regional
  ADD COLUMN IF NOT EXISTS usa_macrorregiao_cts boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN input.unidade_regional.usa_macrorregiao_cts IS
  'Politica da unidade: marcado, ela usa macrorregiao de CTS e CADA sistema dela '
  'aceita uma CTS so; desmarcado, aceitam varias. E regra de cadastro — o motor '
  'nao le esta coluna.';

ALTER TABLE input.cidade_sistema
  DROP COLUMN IF EXISTS usa_sistema_cts;


-- A TRILHA GANHA O TIPO `unidade`, e o tipo `sistema` FICA.
--
-- Ficar nao e conservadorismo: ha 142 linhas de trilha com `tipo = 'sistema'`, e
-- todas dizem quem marcou ou desmarcou a caixa quando ela era do sistema. Tirar
-- o tipo do CHECK invalidaria linhas ja gravadas — e apagar a unica resposta
-- para "por que este sistema recusou a segunda CTS em agosto".
--
-- O QUE MUDA e para onde a proxima linha vai: `ficha_id` passa a ser o
-- `unidade_id`, com o mesmo campo `usaCts`. Quem ler a trilha ve a caixa mudando
-- de dono na data desta migracao, que e exatamente o que aconteceu.
ALTER TABLE input.override DROP CONSTRAINT IF EXISTS override_tipo_check;

ALTER TABLE input.override ADD CONSTRAINT override_tipo_check
  CHECK (tipo = ANY (ARRAY['sub-bacia'::text, 'cts'::text, 'ete'::text,
                           'cidade'::text, 'topologia'::text, 'sistema'::text,
                           'empresa'::text, 'unidade'::text]));

COMMENT ON COLUMN input.override.tipo IS
  'Que ficha mudou: sub-bacia, cts, ete, cidade, topologia, sistema, empresa ou '
  'unidade. Em `topologia`, `ficha_id` e o componente (campos `sisId` e `jusante`); '
  'em `unidade`, e a unidade (campo `usaCts`). O tipo `sistema` e HISTORICO: ate a '
  'migracao 016 o `usaCts` era declarado por sistema.';
