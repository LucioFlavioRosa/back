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
    """
    async with pool().acquire() as con:
        async with con.transaction():
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
    return faltam


async def saudavel() -> bool:
    try:
        async with pool().acquire() as con:
            return await con.fetchval("SELECT 1") == 1
    except Exception:
        return False
