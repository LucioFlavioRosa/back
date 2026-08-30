-- A linhagem da análise de sensibilidade, e o modo ESTIMATIVA
--
-- Três colunas em `controle.run_request`, todas para a mesma pergunta: "quais
-- rodadas formam a curva de sensibilidade desta aqui, e quais delas são
-- estimativa rápida em vez de simulação".
--
-- ## Por que não em `params`
--
-- Mesma razão do `rotulo` (migração 004), e uma a mais. `params` é o que vai
-- para o MOTOR, e uma chave a mais lá quebra a execução. Além disso `params` é o
-- que o `digest` compara para deduplicar: gravar a linhagem ali faria duas
-- rodadas de conteúdo idêntico deixarem de ser idênticas por causa de quem as
-- pediu — a dedupe pararia de funcionar exatamente onde ela é útil.
--
-- ## Por que não pelo NOME da rodada
--
-- Foi a primeira versão, e ela era frágil de um jeito que não aparece em teste:
-- o front escrevia "sensibilidade +10% · base 85cbc6" no rótulo e lia o degrau
-- de volta com uma expressão regular. Duas coisas quebravam. O rótulo é livre e
-- editável, então qualquer edição desmanchava a curva em silêncio. E a dedupe do
-- backend é por PARÂMETROS: quando a mesma variação já existia com outro nome, o
-- servidor devolvia a rodada existente e o front não a reconhecia — a análise
-- ficava eternamente "não rodou" com o resultado pronto no banco.
--
-- Com `base_run_id` a pergunta vira uma consulta, e o rótulo volta a ser o que
-- ele é: texto para humano ler.
--
-- ## `estimativa`
--
-- Rodada de sensibilidade em modo rápido: solver com teto de 60s em vez de 1000s.
-- Ela é uma execução de verdade — entra na fila, ocupa o executor, publica
-- resultado —, mas NÃO é uma simulação que alguém deva comparar com as outras no
-- histórico. Serve para ler a INCLINAÇÃO da curva, não para decidir um plano.
--
-- Por isso ela é excluída de `GET /runs`. Misturá-la ali criaria a pior
-- confusão possível numa tela de decisão de CAPEX: duas linhas com o mesmo
-- orçamento e VPLs diferentes, sem nada na tela dizendo que uma parou no relógio
-- e a outra provou otimalidade.
--
-- O default é `false`, então toda rodada que já existe continua aparecendo.

ALTER TABLE controle.run_request
  ADD COLUMN IF NOT EXISTS base_run_id    text,
  ADD COLUMN IF NOT EXISTS variacao_fator double precision,
  ADD COLUMN IF NOT EXISTS estimativa     boolean NOT NULL DEFAULT false;

-- A consulta da curva é sempre "as variações DESTA base", e ela roda a cada
-- abertura do nível 1. Sem o índice é varredura da tabela inteira.
-- Parcial: a esmagadora maioria das rodadas não é variação de ninguém.
CREATE INDEX IF NOT EXISTS run_request_base_run_id_idx
    ON controle.run_request (base_run_id)
 WHERE base_run_id IS NOT NULL;

COMMENT ON COLUMN controle.run_request.base_run_id IS
  'A rodada de que esta é uma variação de orçamento. NULL para rodada comum.';
COMMENT ON COLUMN controle.run_request.variacao_fator IS
  'O multiplicador do orçamento (1.1 = +10%). É daqui que sai o degrau da curva.';
COMMENT ON COLUMN controle.run_request.estimativa IS
  'Rodada de sensibilidade em modo rápido (solver curto). Não aparece no histórico.';
