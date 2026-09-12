-- A LINHA DA MACRORREGIAO GANHA COMO SE DECLARAR.
--
-- A macrorregiao vira uma linha de `input.cts_operacional` no instante em que e
-- COLOCADA num sistema (`salvar_topologia`). Ate la ela e so uma soma calculada
-- na leitura; colocada, precisa de linha de verdade, porque:
--
--   * `componentes_cts_capex.cts` e FK para `cts_operacional(cts)`, e sao as 4
--     obras da macrorregiao que se vao preencher;
--   * o motor le `SELECT * FROM input.cts_operacional` e monta o no a partir da
--     topologia — sem a linha, o no colocado nao teria ficha nenhuma.
--
-- POR QUE UMA COLUNA, E NAO DEDUZIR PELO NOME. Sem marca, "esta linha e uma
-- macrorregiao?" so se responde cruzando `cts` com os `sistema_cts` dos outros —
-- identidade por coincidencia de nome, que e exatamente o tipo de regra que
-- passa a errar no dia em que a origem batizar um coletor com o nome de uma
-- macrorregiao. Com a coluna, cada consulta diz o que quer dizer.
--
-- A LINHA DA MACRORREGIAO NAO E MEMBRO DE NENHUMA: `sistema_cts` fica NULA nela.
-- Fosse preenchida, `agrupar` a devolveria junto dos membros e a soma contaria
-- tudo duas vezes. As duas colunas juntas dizem os tres estados que existem:
--
--   sistema_cts   e_macrorregiao   o que a linha e
--   -----------   --------------   ----------------------------------------
--   NULA          false            coletor comum, fora de macrorregiao
--   preenchida    false            coletor MEMBRO da macrorregiao nomeada
--   NULA          true             a MACRORREGIAO — soma dos membros dela
--
-- `false` E O DEFAULT E VALE PARA TODA LINHA QUE VEIO DO DATABRICKS: a origem
-- entrega coletores, e nunca agregados.
ALTER TABLE input.cts_operacional
  ADD COLUMN IF NOT EXISTS e_macrorregiao boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN input.cts_operacional.e_macrorregiao IS
  'Esta linha e a macrorregiao (a soma dos coletores com o mesmo `sistema_cts`), '
  'criada pelo backend ao coloca-la num sistema — e nao um coletor vindo do '
  'Databricks. Nela `sistema_cts` e NULA: a macrorregiao nao e membro de si mesma.';

-- O ESTADO IMPOSSIVEL FICA IMPOSSIVEL. Uma linha marcada como macrorregiao COM
-- `sistema_cts` seria membro e agregado ao mesmo tempo — a soma se incluiria.
ALTER TABLE input.cts_operacional
  DROP CONSTRAINT IF EXISTS macrorregiao_nao_e_membro;
ALTER TABLE input.cts_operacional
  ADD CONSTRAINT macrorregiao_nao_e_membro
  CHECK (NOT (e_macrorregiao AND sistema_cts IS NOT NULL));

-- As duas leituras que a tela faz sao "quais macrorregioes existem" e "esta
-- linha e uma delas?". Sao poucas linhas marcadas entre milhares, entao o indice
-- parcial e pequeno e serve as duas.
CREATE INDEX IF NOT EXISTS ix_cts_e_macrorregiao
  ON input.cts_operacional (cts)
  WHERE e_macrorregiao;
