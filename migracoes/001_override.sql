-- Trilha de auditoria do cadastro.
--
-- POR QUE ELA EXISTE
-- Parte do cadastro vem do Databricks e é travada na tela. Quando a Regional
-- discorda de um número, ela sobrescreve — e o contrato do front (`DEPLOY.md` §3)
-- manda a correção junto com a ficha, com valor antigo, valor novo, autor e
-- instante, "na mesma transação do dado", para nunca existir dado corrigido sem
-- trilha.
--
-- Até esta migração não havia onde gravar isso. O backend tinha duas saídas: criar
-- a tabela, ou aceitar a ficha e descartar a trilha em silêncio — e prometer
-- auditoria que não existe é pior que não ter auditoria, porque alguém vai confiar
-- nela numa discussão sobre um número.
--
-- O QUE ELA NÃO É
-- Não é histórico de edição: só entra o que sobrescreve valor VINDO DO DATABRICKS.
-- Campo que a Regional preenche (o bloco `params` da ficha) não gera linha — não
-- há valor anterior de outra fonte para contrastar.
--
-- Também não é versionamento da ficha: a ficha é substituída inteira a cada PUT
-- (idempotente). A trilha responde "quem mudou este campo, quando, de quanto para
-- quanto", que é a pergunta que aparece meses depois, na reunião.
--
-- É APPEND-ONLY. A escrita nunca apaga linha daqui: só acrescenta quando o valor
-- muda em relação à última linha daquele campo. A primeira versão do backend
-- apagava a trilha da ficha e regravava o conjunto atual, e uma revisão mostrou o
-- estrago — correção feita em julho reaparecia com data de agosto. Auditoria que
-- reescreve a data do fato não é auditoria.

CREATE TABLE IF NOT EXISTS input.override (
    override_id   bigserial PRIMARY KEY,

    -- Que ficha. `tipo` + `ficha_id` em vez de uma FK por tabela: são cinco
    -- tabelas de ficha e uma FK para cada faria a trilha crescer junto com o
    -- cadastro. O preço é não ter integridade referencial aqui — aceitável,
    -- porque a trilha é registro do que aconteceu, e sobrevive à ficha ser apagada.
    tipo          text NOT NULL
        CHECK (tipo IN ('sub-bacia', 'cts', 'ete', 'cidade')),
    ficha_id      text NOT NULL,
    -- Sem CASCADE, e de proposito. O comentario desta tabela diz que a trilha
    -- sobrevive a ficha ser apagada; com CASCADE, apagar a unidade levaria a
    -- auditoria junto — que e exatamente o momento em que alguem vai querer
    -- consulta-la. RESTRICT obriga a decisao a ser explicita.
    unidade_id    text NOT NULL
        REFERENCES input.unidade_regional(unidade_id) ON DELETE RESTRICT,

    campo         text NOT NULL,
    valor_antigo  text,             -- como veio do Databricks; null = campo vazio lá
    valor_novo    text NOT NULL,    -- o que a Regional gravou

    autor         text NOT NULL,
    gravado_em    timestamptz NOT NULL DEFAULT now()
);

-- A consulta natural é "o que foi corrigido nesta ficha", e a segunda é "o que
-- esta pessoa corrigiu". As duas aparecem em auditoria.
CREATE INDEX IF NOT EXISTS ix_override_ficha
    ON input.override (tipo, ficha_id, gravado_em DESC);
CREATE INDEX IF NOT EXISTS ix_override_unidade
    ON input.override (unidade_id, gravado_em DESC);

COMMENT ON TABLE input.override IS
    'Trilha de auditoria: cada dado do Databricks sobrescrito pela Regional. '
    'Gravada na MESMA transacao da ficha (ver DEPLOY.md secao 3 do front).';
