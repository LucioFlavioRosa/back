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


async def saudavel() -> bool:
    try:
        async with pool().acquire() as con:
            return await con.fetchval("SELECT 1") == 1
    except Exception:
        return False
