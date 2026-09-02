-- A CTS PASSA A DIZER EM QUE CIDADE ELA ESTA.
--
-- Ate aqui `cts_operacional` nao tinha coluna de lugar nenhuma: so o id e os
-- numeros operacionais. A consequencia estava escrita no proprio codigo, em
-- `cadastro.py`, como se fosse regra de negocio:
--
--   "COMPONENTE SEM SISTEMA — NAO e recortado por unidade, e nao poderia ser:
--    sem sistema nao ha cidade, nao ha empresa, nao ha unidade."
--
-- A premissa e falsa, e e essa a correcao. A fonte SEMPRE soube onde a CTS
-- esta: o extrato de portfolio traz REGIONAL, DIRETORIA, UNIDADE, EMP_CODIGO,
-- EMPRESA, CIDADE e CTS na mesma linha. Quem perdeu a informacao foi o esquema.
--
-- POR QUE UMA COLUNA SO, E NAO CINCO. A cidade determina as outras quatro:
-- `cidade_empresa` liga cidade a empresa (141 cidades, 141 linhas — cada cidade
-- tem UMA empresa), a empresa tem unidade, a unidade tem diretoria e regional.
-- Gravar os cinco niveis seria guardar quatro respostas derivaveis e criar
-- quatro maneiras de o cadastro se contradizer.
ALTER TABLE input.cts_operacional
  ADD COLUMN IF NOT EXISTS cidade_id text REFERENCES input.cidade(cidade_id);

COMMENT ON COLUMN input.cts_operacional.cidade_id IS
  'Onde a CTS esta. Determina empresa, unidade, diretoria e regional pela '
  'hierarquia. Nulo = a carga ainda nao trouxe: a tela mostra essas CTS num '
  'grupo a parte em vez de escondê-las.';

CREATE INDEX IF NOT EXISTS ix_cts_cidade ON input.cts_operacional (cidade_id);

-- O BACKFILL VEM DA SOBREPOSICAO DE AREA (`subbacia_cts`), e nao do sistema em
-- que a CTS esta hoje.
--
-- `subbacia_cts` pareia CTS e sub-bacia por SOBREPOSICAO DE AREA — nunca
-- significou pertencimento, e e por isso que ela nao serve para dizer em que
-- sistema a CTS entra. Mas para dizer ONDE ELA ESTA e exatamente o sinal certo:
-- e um fato geografico. A sub-bacia esta num sistema, o sistema esta numa
-- cidade, e a CTS que se sobrepoe aquela sub-bacia esta naquela cidade.
--
-- A REGRA FOI CONFERIDA CONTRA OS DADOS antes de virar migracao:
--
--   * localiza as 337 CTS da base, colocadas e soltas;
--   * NENHUMA cai em duas cidades — nao ha empate para desempatar;
--   * das 188 ja colocadas num sistema, a cidade por area bate com a cidade do
--     sistema em 186.
--
-- AS DUAS QUE NAO BATEM (`cts_001` e `cts_002`) estao num sistema de `d1c14` e
-- fisicamente em `d1c10` e `d1c8`. Nao sao ruido: sao o defeito que esta
-- migracao existe para impedir — o seletor sem recorte deixava colocar uma CTS
-- num sistema de outra cidade. A TOPOLOGIA DELAS NAO E TOCADA AQUI. Desfazer
-- uma escolha que alguem fez, por causa de uma regra que so passa a valer
-- agora, seria mudar o plano de quem cadastrou sem avisar; o lugar de corrigir
-- e a tela, com a pessoa vendo.
UPDATE input.cts_operacional o
   SET cidade_id = loc.cidade_id
  FROM (SELECT DISTINCT sc.cts, cs.cidade_id
          FROM input.subbacia_cts sc
          JOIN input.sistema_topologia st
            ON st.componente_sistema_id = sc.sub_bacia
          JOIN input.cidade_sistema cs ON cs.sistema_id = st.sistema_id) AS loc
 WHERE loc.cts = o.cts
   AND o.cidade_id IS NULL;


-- ONDE CADA CTS ESTA, NOS CINCO NIVEIS, COLOCADA OU NAO.
--
-- A pergunta do produto e "em que sistemas posso associar esta CTS", e ela se
-- responde pela cidade. Mas quem investiga precisa ver a cadeia inteira, e
-- montar o JOIN de quatro tabelas em toda consulta e a receita para duas
-- consultas discordarem uma da outra.
--
-- A VISAO NAO GUARDA NADA: os cinco niveis sao DERIVADOS de `cidade_id`. Gravar
-- regional/diretoria/unidade/empresa em `cts_operacional` criaria quatro copias
-- de uma resposta que a hierarquia ja da — e quatro maneiras de o cadastro se
-- contradizer quando uma cidade mudar de empresa.
--
-- `sistema_id` VEM JUNTO, e nulo quando a CTS ainda nao foi colocada: e a
-- diferenca entre "esta livre" e "ja tem dono", que e a primeira coisa que se
-- pergunta depois de saber onde ela esta.
CREATE OR REPLACE VIEW input.cts_localizacao AS
SELECT o.cts,
       NULLIF(t.sistema_id, '')          AS sistema_id,
       o.cidade_id,
       c.cidade_name,
       ce.emp_codigo,
       e.empresa,
       e.unidade_id,
       u.unidade_name,
       u.diretoria_id,
       u.diretoria_name,
       u.regional_id,
       u.regional_name
  FROM input.cts_operacional o
  LEFT JOIN input.sistema_topologia t ON t.componente_sistema_id = o.cts
  LEFT JOIN input.cidade c            ON c.cidade_id  = o.cidade_id
  LEFT JOIN input.cidade_empresa ce   ON ce.cidade_id = o.cidade_id
  LEFT JOIN input.empresa e           USING (emp_codigo)
  LEFT JOIN input.unidade_regional u  ON u.unidade_id = e.unidade_id;

COMMENT ON VIEW input.cts_localizacao IS
  'Onde cada CTS esta nos cinco niveis (regional, diretoria, unidade, empresa, '
  'cidade), colocada ou nao. Tudo derivado de cts_operacional.cidade_id.';
