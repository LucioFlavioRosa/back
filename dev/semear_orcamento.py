"""Semeia `input.orcamento` — a verba anual por regional.

POR QUE ISTO É UM SCRIPT DE `dev/` E NÃO PARTE DA MIGRAÇÃO 014.

A migração cria a coluna `ano` e a chave (regional_id, ano); ela é estrutura, e
roda em qualquer banco, produção inclusive. Já o CONTEÚDO desta tabela vem do
Databricks na carga seguinte — e uma migração que insere verba inventada num
banco de produção afirmaria um orçamento que ninguém aprovou.

Aqui é diferente: o banco local nasce vazio nesta tabela, e o modelo de dados v8
a descreve como uma das entradas do problema ("orçamento anual por regional e
metas de cobertura por município e ano viram restrições"). Sem nenhuma linha, o
fluxo fica com um vão — dá para simular, porque a tela manda o orçamento no
próprio pedido, mas não dá para ver de onde ele deveria sair.

O QUE ELE ESCREVE: o mesmo cronograma que a tela de simulação já oferece como
padrão (60, 60, 50…), replicado para cada regional. É um número plausível e
reconhecível — quem vir 60 em 2026 sabe de onde veio —, e não uma constante
mágica que pareça medida.

Idempotente: `ON CONFLICT DO NOTHING` na chave (regional_id, ano).
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

#: O cronograma padrão da tela de simulação (`ORCAMENTO_PADRAO` em
#: `src/rodada/domain/simulacao.ts`), em milhões de reais. Repetido aqui de
#: propósito: são dois lados diferentes do produto, e amarrá-los por import não
#: faria sentido — mas mantê-los iguais faz o número da tela e o do banco se
#: reconhecerem.
CRONOGRAMA = [
    (2026, 60), (2027, 60), (2028, 50), (2029, 50), (2030, 50), (2031, 50),
    (2032, 40), (2033, 40), (2034, 30), (2035, 30), (2036, 30), (2037, 20),
    (2038, 20), (2039, 20), (2040, 10),
]

MILHAO = 1_000_000

URL = os.environ.get(
    "POSTGRES_URL", "postgresql://otim:otim@localhost:55432/otimizador"
)

#: Onde ele aceita escrever. O script insere verba INVENTADA, e `POSTGRES_URL`
#: e uma variavel de ambiente comum — quem tiver a de producao exportada na
#: sessao roda isto por engano e planta orcamento ficticio numa tabela que
#: ninguem mais olha com desconfianca. A lista abaixo e a trava; `--forcar`
#: existe para o caso legitimo de um banco de homologacao com outro host, e
#: obriga quem o usa a dizer isso em voz alta.
HOSTS_PERMITIDOS = ("localhost", "127.0.0.1", "::1", "db", "postgres")


def _host(url: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower()


async def main() -> None:
    if _host(URL) not in HOSTS_PERMITIDOS and "--forcar" not in sys.argv:
        print(
            f"Recusado: POSTGRES_URL aponta para {_host(URL)!r}, que nao esta na "
            f"lista de hosts locais {HOSTS_PERMITIDOS}.\n"
            "Este script escreve orcamento de exemplo e nao deve tocar em base "
            "real. Se for mesmo o que voce quer, repita com --forcar."
        )
        raise SystemExit(1)

    con = await asyncpg.connect(URL)
    try:
        regionais = [r["regional_id"] for r in await con.fetch(
            "SELECT regional_id FROM input.regional ORDER BY regional_id"
        )]
        if not regionais:
            print("Nenhuma regional em `input.regional` — rode a migração 014 antes.")
            return

        linhas = [
            (rid, ano, valor * MILHAO)
            for rid in regionais
            for ano, valor in CRONOGRAMA
        ]
        await con.executemany(
            "INSERT INTO input.orcamento (regional_id, ano, valor_ano)"
            " VALUES ($1, $2, $3) ON CONFLICT (regional_id, ano) DO NOTHING",
            linhas,
        )

        for r in await con.fetch(
            "SELECT regional_id, COUNT(*) AS anos,"
            "       SUM(valor_ano) / 1e6 AS total_mi,"
            "       MIN(ano) AS de, MAX(ano) AS ate"
            "  FROM input.orcamento GROUP BY 1 ORDER BY 1"
        ):
            print(
                f"  {r['regional_id']}: {r['anos']} anos "
                f"({r['de']}–{r['ate']}), R$ {r['total_mi']:.0f} Mi"
            )
    finally:
        await con.close()


if __name__ == "__main__":
    asyncio.run(main())
