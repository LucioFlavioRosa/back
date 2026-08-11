-- Trilha de auditoria do cadastro: cria a tabela `input.override`.
--
-- >> ESTE ARQUIVO CRIA A TABELA. QUEM DEFINE O ALCANCE DELA É A 007. <<
-- Leia os dois: a 007 acrescenta `origem`, deixa `valor_novo` aceitar NULL e
-- reescreve os COMMENT desta tabela. Onde os dois divergirem, vale a 007.
--
-- POR QUE ELA EXISTE
-- Cadastro corrigido sem registro de quem corrigiu é número sem dono, e alguém
-- vai discutir esse número meses depois. A trilha responde "quem mudou este
-- campo, quando, de quanto para quanto" — e é gravada na MESMA transação do dado,
-- para nunca existir correção sem rastro.
--
-- O QUE ELA NÃO É
-- Não é versionamento da ficha: a ficha é substituída inteira a cada PUT
-- (idempotente), e aqui ficam os campos que mudaram, um por linha.
--
-- É APPEND-ONLY. A escrita nunca apaga linha daqui, só acrescenta. Uma trilha que
-- se reescreve não é trilha: correção feita em julho não pode reaparecer com data
-- de agosto.

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
    valor_antigo  text,             -- null = o campo/registro não existia antes
    -- O NOT NULL aqui é o estado desta migração, e SAI na 007: a mudança pode ser
    -- a linha deixar de existir, e null passa a significar "foi removido".
    valor_novo    text NOT NULL,

    autor         text NOT NULL,
    gravado_em    timestamptz NOT NULL DEFAULT now()
);

-- A consulta natural é "o que foi corrigido nesta ficha", e a segunda é "o que
-- esta pessoa corrigiu". As duas aparecem em auditoria.
CREATE INDEX IF NOT EXISTS ix_override_ficha
    ON input.override (tipo, ficha_id, gravado_em DESC);
CREATE INDEX IF NOT EXISTS ix_override_unidade
    ON input.override (unidade_id, gravado_em DESC);

-- A 007 reescreve este COMMENT. Ele fica aqui para a tabela nunca existir sem
-- descricao, mesmo num banco que so tenha chegado ate esta migracao.
COMMENT ON TABLE input.override IS
    'Trilha de auditoria do cadastro: cada campo alterado, com valor anterior, '
    'valor novo, autor e instante. Append-only, gravada na MESMA transacao do dado.';
