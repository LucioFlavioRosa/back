-- `controle.run_comentario` — a anotação de quem ANALISA a rodada.
--
-- É outra coisa que o `rotulo` (migração 004). O rótulo é o nome dado no disparo,
-- antes de existir resultado: ele descreve a INTENÇÃO. O comentário é escrito
-- depois, olhando o que saiu, e descreve a CONCLUSÃO — "esta foi a melhor porque
-- o pico de 2029 some". Um não substitui o outro, e forçar os dois no mesmo campo
-- perderia o de baixo: o rótulo aparece na lista, e ninguém quer duas linhas de
-- texto em cada item dela.
--
-- TABELA PRÓPRIA, E SEM FK, pela mesma razão de `run_favorita` (009): não há uma
-- tabela única com todas as rodadas para apontar. A rodada nasce em
-- `controle.run_request` e publica em `public.otim_meta`, e existe um terceiro
-- caso — rodada publicada direto pelo pacote, sem passar pela fila — que só tem
-- linha na segunda. Uma coluna em `run_request` deixaria justamente essas sem
-- poder receber comentário, e elas aparecem na lista como todas as outras.
--
-- A consequência de não ter FK é a mesma de lá, e está tratada no mesmo lugar:
-- `resultado.excluir()` apaga esta tabela explicitamente, senão o comentário
-- sobreviveria à rodada.
--
-- UM comentário por rodada, não um por pessoa. É anotação COMPARTILHADA — quem
-- abre a rodada amanhã precisa ler o que se concluiu dela, e uma pilha de notas
-- privadas não serviria a isso. Por isso `autor` e `atualizado_em` são gravados:
-- num campo que todos podem reescrever, quem escreveu e quando é o mínimo para o
-- texto significar alguma coisa.
--
-- É a ÚNICA parte mutável de uma rodada. O resto é imutável de propósito (o
-- `run_id` congela no primeiro SUCESSO, republicar é 409), e essa diferença é
-- deliberada: o comentário não é registro da execução, é leitura humana sobre
-- ela. Não entra em `params` nem em `otim_meta` por isso mesmo — nada aqui pode
-- mudar o que a rodada foi.

CREATE TABLE IF NOT EXISTS controle.run_comentario (
  run_id        text PRIMARY KEY,
  texto         text NOT NULL,
  autor         text,
  atualizado_em timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE controle.run_comentario IS
  'Anotação humana sobre a rodada, escrita depois de ver o resultado. Uma por rodada, compartilhada, editável — a única parte mutável de uma rodada. Sem FK: não há tabela única com todas as rodadas (ver 009_favoritas.sql).';

COMMENT ON COLUMN controle.run_comentario.texto IS
  'Texto livre. Vazio não é gravado: apagar o texto apaga a linha, para "sem comentário" ter uma representação só.';

COMMENT ON COLUMN controle.run_comentario.autor IS
  'Quem escreveu por último. Não é dono: qualquer pessoa que enxerga a rodada pode reescrever.';
