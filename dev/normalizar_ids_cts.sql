-- =====================================================================
-- O ID DA CTS DEIXA DE CARREGAR A SUB-BACIA.
--
--     cts_d1b100_1_1  ->  cts_001        (nome na tela: "CTS 001")
--
-- Os ids vieram assim da planilha do Databricks, de uma época em que CTS e
-- sub-bacia eram a mesma coisa vista de dois ângulos. Hoje não são: a CTS
-- pertence ao SISTEMA (linha em `sistema_topologia`), e `subbacia_cts` é
-- sobreposição de área, não pertinência. Um id que diz `d1b100` afirma um
-- vínculo que o modelo não tem mais — e é a primeira coisa que alguém lê.
--
-- ## Por que isto é script, e não migração
--
-- `migracoes/` é conferido por estrutura (a coluna existe? a constraint lista o
-- valor?) em `app/infra/db.py`. Renomear DADO não deixa marca estrutural: não há
-- o que procurar para dizer "aplicada". Além disso a próxima carga do Databricks
-- traz os ids antigos de volta — então isto precisa ser RE-EXECUTÁVEL, e não
-- aplicado uma vez e esquecido.
--
-- É idempotente: quem já está no padrão fica como está e tem o número reservado.
-- Rodar duas vezes seguidas não muda nada na segunda.
--
-- ## O que ele NÃO toca, de propósito
--
-- `public.otim_*` — os resultados das rodadas já executadas. Eles são o registro
-- de uma corrida passada, carimbado com o `banco_md5` da base daquele momento;
-- reescrever os ids ali faria o resultado mentir sobre a própria procedência. As
-- rodadas antigas seguem exibindo os ids antigos, que é o que elas de fato
-- rodaram. As próximas já nascem com os novos.
--
-- ## Uso
--
--   docker exec -i otimizador-backend-db-1 \
--     psql -U otim -d otimizador -v ON_ERROR_STOP=1 < dev/normalizar_ids_cts.sql
-- =====================================================================

\set ON_ERROR_STOP on
BEGIN;

/*
 * TRAVA AS TABELAS ANTES DE LER QUALQUER COISA.
 *
 * Sem isto há uma janela real: uma requisição que já leu o id ANTIGO pode gravar
 * trilha (`input.override`, que não tem FK) depois do `UPDATE` daqui, e o órfão
 * nasce de novo — desta vez sem conserto fácil, porque a segunda execução não
 * consegue reconstruir o mapa antigo→novo: os ids antigos já sumiram de
 * `cts_operacional`. Foi assim que as 40 primeiras órfãs precisaram de um backup
 * para serem reparadas.
 *
 * `EXCLUSIVE` e não `ACCESS EXCLUSIVE`: leitura continua passando (o `SELECT` de
 * uma tela aberta não trava), e só a ESCRITA espera. O rename leva menos de um
 * segundo em 337 CTS.
 *
 * O ideal ainda é rodar com a API parada — isto é a rede, não o plano.
 */
LOCK TABLE input.cts_operacional,
           input.componentes_cts_capex,
           input.subbacia_cts,
           input.sistema_topologia,
           input.override
      IN EXCLUSIVE MODE;

CREATE TEMP TABLE renome (
  antigo text PRIMARY KEY,
  novo   text NOT NULL UNIQUE   -- colisão aborta a transação, e é para abortar
) ON COMMIT DROP;

-- 1. O id passa a espelhar o NOME que a tela já mostra.
--    `CTS 042` -> `cts_042`. É a correspondência mais útil possível: quem lê
--    "CTS 042" na tela acha a linha no banco sem tradução.
INSERT INTO renome (antigo, novo)
SELECT o.cts,
       'cts_' || lpad(substring(t.componente_sistema_nome FROM '^CTS ([0-9]+)$'), 3, '0')
  FROM input.cts_operacional o
  JOIN input.sistema_topologia t ON t.componente_sistema_id = o.cts
 WHERE t.componente_sistema_nome ~ '^CTS [0-9]+$';

-- 2. CTS sem nome no padrão (ainda não colocada em sistema nenhum, ou nomeada à
--    mão): número livre depois do maior já usado. Sem isto, uma CTS nova ficaria
--    com o id antigo para sempre.
INSERT INTO renome (antigo, novo)
SELECT o.cts,
       'cts_' || lpad((m.maior + row_number() OVER (ORDER BY o.cts))::text, 3, '0')
  FROM input.cts_operacional o
 CROSS JOIN LATERAL (
       SELECT coalesce(max(substring(novo FROM '^cts_([0-9]+)$')::int), 0) AS maior
         FROM renome
       ) m
 WHERE NOT EXISTS (SELECT 1 FROM renome r WHERE r.antigo = o.cts);

-- 3. Quem já está certo sai da lista — é o que torna a re-execução inócua.
DELETE FROM renome WHERE antigo = novo;

-- 4. Um id novo não pode ser o id de uma CTS que NÃO vai ser renomeada: o
--    UPDATE do passo 6 estouraria a chave primária no meio, e a mensagem do
--    banco não diria qual das 337 causou.
DO $$
DECLARE conflito text;
BEGIN
  SELECT r.novo INTO conflito
    FROM renome r
    JOIN input.cts_operacional o ON o.cts = r.novo
   WHERE NOT EXISTS (SELECT 1 FROM renome x WHERE x.antigo = o.cts)
   LIMIT 1;
  IF conflito IS NOT NULL THEN
    RAISE EXCEPTION 'id novo % ja pertence a outra CTS que nao sera renomeada', conflito;
  END IF;
END $$;

-- 5. As FKs saem para o rename e voltam antes do COMMIT.
--    Não são DEFERRABLE, então seriam checadas a cada UPDATE: a primeira linha
--    da tabela pai já deixaria as filhas apontando para um id inexistente.
ALTER TABLE input.componentes_cts_capex DROP CONSTRAINT componentes_cts_capex_cts_fkey;
ALTER TABLE input.subbacia_cts          DROP CONSTRAINT subbacia_cts_cts_fkey;

-- 6. Todo lugar do CADASTRO que guarda o id.
UPDATE input.cts_operacional o
   SET cts = r.novo             FROM renome r WHERE o.cts = r.antigo;
UPDATE input.componentes_cts_capex c
   SET cts = r.novo             FROM renome r WHERE c.cts = r.antigo;
UPDATE input.subbacia_cts s
   SET cts = r.novo             FROM renome r WHERE s.cts = r.antigo;

-- A topologia guarda o id como TEXTO, sem FK — é o nó do caminho até a ETE.
UPDATE input.sistema_topologia t
   SET componente_sistema_id = r.novo
  FROM renome r WHERE t.componente_sistema_id = r.antigo;
UPDATE input.sistema_topologia t
   SET componente_sistema_id_jusante = r.novo
  FROM renome r WHERE t.componente_sistema_id_jusante = r.antigo;

-- A trilha de auditoria vai junto: sem isto, o histórico de edição da CTS 042
-- apontaria para uma ficha que não existe mais, e a tela de trilha viria vazia
-- numa CTS que foi editada.
--
-- SEM filtrar por `tipo`, de propósito. A primeira versão filtrava `tipo='cts'` e
-- deixou 40 linhas para trás: a gravação de topologia registra com
-- `tipo='topologia'` e `ficha_id` = id do COMPONENTE, então o histórico de
-- "colocou/tirou a CTS do sistema" ficou preso ao id antigo. Casar só pelo
-- `ficha_id` é seguro — `renome.antigo` só contém id de CTS, que não colide com
-- id de sub-bacia, ETE, cidade ou sistema — e continua correto no dia em que
-- aparecer um `tipo` novo que também aponte para um componente.
UPDATE input.override ov
   SET ficha_id = r.novo
  FROM renome r WHERE ov.ficha_id = r.antigo;

ALTER TABLE input.componentes_cts_capex
  ADD CONSTRAINT componentes_cts_capex_cts_fkey
  FOREIGN KEY (cts) REFERENCES input.cts_operacional(cts);
ALTER TABLE input.subbacia_cts
  ADD CONSTRAINT subbacia_cts_cts_fkey
  FOREIGN KEY (cts) REFERENCES input.cts_operacional(cts);

-- 7. O que aconteceu, em números.
SELECT count(*) AS renomeadas FROM renome;

COMMIT;

-- 8. Conferência DEPOIS do commit: nenhum id de CTS pode ter sobrado com a
--    marca da sub-bacia, e as três tabelas têm de continuar casando.
SELECT count(*) FILTER (WHERE cts !~ '^cts_[0-9]+$') AS fora_do_padrao,
       count(*)                                      AS total
  FROM input.cts_operacional;

SELECT count(*) AS obras_orfas
  FROM input.componentes_cts_capex c
 WHERE NOT EXISTS (SELECT 1 FROM input.cts_operacional o WHERE o.cts = c.cts);

SELECT count(*) AS cts_na_topologia_sem_ficha
  FROM input.sistema_topologia t
 WHERE t.componente_sistema_id LIKE 'cts%'
   AND NOT EXISTS (SELECT 1 FROM input.cts_operacional o WHERE o.cts = t.componente_sistema_id);

-- A trilha, POR TIPO. Foi aqui que a primeira versão falhou em silêncio: as
-- tabelas de cadastro casavam, e só a auditoria tinha ficado para trás. Quebrar
-- por tipo é o que mostra QUAL trilha ficou — um total agregado diria "0 órfãos"
-- se um tipo compensasse o outro.
SELECT tipo,
       count(*) AS linhas,
       count(*) FILTER (
         WHERE ficha_id LIKE 'cts%' AND ficha_id !~ '^cts_[0-9]+$'
       ) AS orfaos
  FROM input.override GROUP BY tipo ORDER BY 1;
