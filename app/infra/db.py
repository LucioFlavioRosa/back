"""Pool de conexoes com o Postgres.

`asyncpg` direto, sem ORM. A razao e o formato do trabalho: quase tudo aqui e
leitura de tabelas ja modeladas por outro processo (o job materializa as 14
`public.otim_*`) e a forma da resposta e ditada pelo `CONTRATO.md`, nao pelo
esquema. Um ORM adicionaria uma terceira modelagem entre as duas que ja existem,
e nenhuma consulta ficaria mais legivel por causa dele.

O pool e criado no lifespan da aplicacao e vive enquanto o processo viver: abrir
conexao por request custa mais que a propria consulta na maioria dos endpoints
deste servico.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from app.config import config

_pool: asyncpg.Pool | None = None


async def abrir_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        cfg = config()
        _pool = await asyncpg.create_pool(
            dsn=cfg.postgres_url,
            min_size=cfg.pool_min,
            max_size=cfg.pool_max,
            # O job pode segurar locks longos durante a publicacao; uma consulta de
            # tela nao deve esperar por eles indefinidamente e deixar o usuario com
            # a roda girando sem explicacao.
            command_timeout=30,
            init=_registrar_json,
        )
    return _pool


async def _registrar_json(con: asyncpg.Connection) -> None:
    """`jsonb` chega como dict, e nao como texto.

    Sem isto o asyncpg devolve `json`/`jsonb` como `str` — e o codigo que faz
    `linha["params_extra"].get("BASE_RECEITA")` estoura com AttributeError na
    primeira rodada publicada. O erro so aparece em runtime, contra banco real, e
    e por isso que passou batido: nenhum teste desta suite abre conexao.

    Vale para TODAS as colunas jsonb do esquema (`params_extra`, `peso_cidade`,
    `orcamento_por_ano`, `capex_componentes`, `detalhe`), e nao so para a que
    doeu primeiro.
    """
    for tipo in ("json", "jsonb"):
        await con.set_type_codec(
            tipo, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )


async def fechar_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("pool nao inicializado — abrir_pool() roda no lifespan")
    return _pool


async def buscar(sql: str, *args: Any) -> list[dict[str, Any]]:
    async with pool().acquire() as con:
        return [dict(r) for r in await con.fetch(sql, *args)]


async def buscar_um(sql: str, *args: Any) -> dict[str, Any] | None:
    async with pool().acquire() as con:
        linha = await con.fetchrow(sql, *args)
        return dict(linha) if linha else None


@asynccontextmanager
async def transacao() -> AsyncIterator[asyncpg.Connection]:
    """Escrita que precisa ser tudo-ou-nada.

    O disparo de uma rodada e o caso: a `run_request` e o `run_status` PENDENTE
    entram juntos, senao existe um instante em que a rodada foi pedida e nao tem
    estado — e o front, que consulta o status logo depois do 201, veria 404.

    `SET LOCAL statement_timeout` e o teto do SERVIDOR, e nao repeticao do
    `command_timeout` do pool. Os dois cobrem coisas diferentes: o do cliente
    cancela a espera de QUEM PEDIU; este aborta a transacao no banco e, com isso,
    DEVOLVE OS LOCKS. A gravacao de topologia segura advisory lock de todos os
    sistemas do envio, entao uma consulta presa aqui prende todo mundo que grava
    naqueles sistemas — e a fila cresce enquanto ninguem ve erro nenhum. `LOCAL`
    porque a conexao volta para o pool e o teto nao deve viajar com ela.
    """
    async with pool().acquire() as con:
        async with con.transaction():
            await con.execute(f"SET LOCAL statement_timeout = {config().statement_timeout_ms}")
            yield con


#: O que o servico EXIGE do banco alem do DDL base — cada linha e uma migracao
#: de `migracoes/`. Sem elas o servico SOBE e so quebra em runtime, com 500
#: obscuro numa rota qualquer: a autorizacao nao encontra `usuario_acesso` e
#: recusa todo mundo, o status nao encontra `progresso` e derruba a tela de
#: acompanhamento. Falha tardia e dificil de ler; aqui ela vira uma frase.
_EXIGIDO = [
    ("input", "override", None, "001_override.sql"),
    ("controle", "run_status", "progresso", "002_progresso.sql"),
    ("controle", "usuario_acesso", None, "003_usuario_acesso.sql"),
    ("controle", "run_request", "rotulo", "004_run_request_rotulo.sql"),
    # `lease_ate` e nao `worker_id`: as duas entram no mesmo ALTER, e e a coluna
    # de PRAZO que o watchdog consulta. Sem ela ele nao tem como distinguir
    # executor trabalhando de executor morto, e a rodada fica RODANDO para sempre.
    ("controle", "run_status", "lease_ate", "008_lease_e_executores.sql"),
    ("controle", "executor", None, "008_lease_e_executores.sql"),
    # `GET /runs` consulta esta tabela em TODA listagem, para marcar a estrela de
    # quem pediu. Sem ela o historico inteiro responde 500 — nao e degradacao, e a
    # tela principal fora do ar. Por isso o /readyz precisa recusar o pod.
    ("controle", "run_favorita", None, "009_favoritas.sql"),
    # Mesma razao da linha de cima, e o mesmo custo: `GET /runs` faz LEFT JOIN
    # nesta tabela em toda listagem, para trazer a anotacao junto da rodada. Sem
    # ela, a lista inteira responde 500 — nao e o comentario que some, e a tela.
    ("controle", "run_comentario", None, "010_run_comentario.sql"),
    # `GET /runs` filtra por `estimativa` em TODA listagem — a coluna e o que
    # mantem a estimativa rapida fora do historico. Sem ela a lista responde 500,
    # e com um WHERE removido "por seguranca" ela responderia coisa pior: as
    # estimativas apareceriam como se fossem simulacoes.
    ("controle", "run_request", "estimativa", "013_estimativa_de_sensibilidade.sql"),
    # As quatro fichas de cadastro, uma linha cada: a migracao acrescenta as duas
    # colunas nas quatro tabelas, e aplicar em tres e o engano provavel. Basta
    # conferir `atualizado_por` — as duas entram no mesmo ALTER, entao uma sem a
    # outra nao e um estado que a migracao produza.
    ("input", "subbacia_operacional", "atualizado_por", "006_auditoria_cadastro.sql"),
    ("input", "cts_operacional", "atualizado_por", "006_auditoria_cadastro.sql"),
    ("input", "ete_capex", "atualizado_por", "006_auditoria_cadastro.sql"),
    ("input", "cidade_operacional", "atualizado_por", "006_auditoria_cadastro.sql"),
]

#: Migracao que nao cria tabela nem coluna: a regra vive numa CONSTRAINT, sobre
#: coluna que ja existia. Procurar a coluna diria "aplicada" com o banco ainda
#: aceitando qualquer `capex` — e e exatamente isso que a migracao existe para
#: impedir. As duas tabelas entram separadas de proposito: aplicar em uma e
#: esquecer a outra e o engano provavel, e ai o nome da que falta e a correcao.
_EXIGIDO_RESTRICAO = [
    ("input", "componentes_subbacias_capex", "capex_e_derivado", "005_capex_derivado.sql"),
    ("input", "componentes_cts_capex", "capex_e_derivado", "005_capex_derivado.sql"),
]

#: Migracao que AFROUXA uma restricao existente — procurar o nome dela nao serve,
#: porque o nome nao muda: `override_tipo_check` existe antes e depois, e so a
#: DEFINICAO passa a listar mais um tipo. Aqui a busca e no texto da restricao.
#:
#: Sem a 011, gravar topologia insere na trilha com `tipo='topologia'` e o banco
#: recusa a transacao INTEIRA — a gravacao falha no fim, depois de validada, com
#: erro de constraint que nao diz nada a quem esta montando o sistema.
_EXIGIDO_NA_RESTRICAO = [
    ("input", "override_tipo_check", "topologia", "011_trilha_da_topologia.sql"),
    ("input", "override_tipo_check", "sistema", "012_trilha_do_sistema.sql"),
]

#: Coluna que precisa ACEITAR NULO. O contrario das listas acima: aqui a coluna
#: sempre existiu, e o que muda e ela deixar de ser NOT NULL.
#:
#: Esta migracao NAO mora em `migracoes/`: `sistema_topologia` e tabela do
#: Databricks, e o DDL dela e do repositorio do motor
#: (`otimizador/infraestrutura/sql/`). O servico depende dela mesmo assim — tirar
#: um componente do sistema grava `sistema_id = NULL` —, entao a ausencia precisa
#: aparecer aqui e nao no meio de um 500.
_EXIGIDO_NULAVEL = [
    ("input", "sistema_topologia", "sistema_id",
     "ddl_input_migracao_05.sql (repositorio do motor)"),
]

#: Mesma razao de `_EXIGIDO_NULAVEL`: tabela do Databricks, DDL no repositorio do
#: motor, e o servico depende dela — a leitura do Grupo 01 seleciona a coluna, e
#: sem ela `GET /hierarquia` responde 500.
_EXIGIDO_COLUNA = [
    ("input", "cidade_sistema", "usa_sistema_cts",
     "ddl_input_migracao_06.sql (repositorio do motor)"),
]


async def migracoes_faltando() -> list[str]:
    """Quais migracoes de `migracoes/` ainda nao foram aplicadas neste banco."""
    faltam = []
    for schema, tabela, coluna, arquivo in _EXIGIDO:
        if coluna is None:
            existe = await buscar_um(
                "SELECT 1 FROM information_schema.tables"
                " WHERE table_schema = $1 AND table_name = $2",
                schema,
                tabela,
            )
        else:
            existe = await buscar_um(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_schema = $1 AND table_name = $2 AND column_name = $3",
                schema,
                tabela,
                coluna,
            )
        if not existe:
            alvo = f"{schema}.{tabela}" + (f".{coluna}" if coluna else "")
            faltam.append(f"{arquivo} (falta {alvo})")
    for schema, tabela, restricao, arquivo in _EXIGIDO_RESTRICAO:
        existe = await buscar_um(
            "SELECT 1 FROM information_schema.table_constraints"
            " WHERE table_schema = $1 AND table_name = $2 AND constraint_name = $3",
            schema,
            tabela,
            restricao,
        )
        if not existe:
            faltam.append(f"{arquivo} (falta {schema}.{tabela} CHECK {restricao})")
    for schema, restricao, termo, arquivo in _EXIGIDO_NA_RESTRICAO:
        existe = await buscar_um(
            "SELECT 1 FROM pg_constraint c"
            "  JOIN pg_namespace n ON n.oid = c.connamespace"
            " WHERE n.nspname = $1 AND c.conname = $2"
            "   AND pg_get_constraintdef(c.oid) LIKE '%' || $3 || '%'",
            schema,
            restricao,
            termo,
        )
        if not existe:
            faltam.append(f"{arquivo} ({restricao} nao aceita '{termo}')")
    for schema, tabela, coluna, arquivo in _EXIGIDO_COLUNA:
        existe = await buscar_um(
            "SELECT 1 FROM information_schema.columns"
            " WHERE table_schema = $1 AND table_name = $2 AND column_name = $3",
            schema,
            tabela,
            coluna,
        )
        if not existe:
            faltam.append(f"{arquivo} (falta {schema}.{tabela}.{coluna})")
    for schema, tabela, coluna, arquivo in _EXIGIDO_NULAVEL:
        nulavel = await buscar_um(
            "SELECT 1 FROM information_schema.columns"
            " WHERE table_schema = $1 AND table_name = $2 AND column_name = $3"
            "   AND is_nullable = 'YES'",
            schema,
            tabela,
            coluna,
        )
        if not nulavel:
            faltam.append(f"{arquivo} ({schema}.{tabela}.{coluna} ainda e NOT NULL)")
    return faltam


async def saudavel() -> bool:
    try:
        async with pool().acquire() as con:
            return await con.fetchval("SELECT 1") == 1
    except Exception:
        return False
