"""Preenche `input.empresa.data_fim_concessao` — o fim da concessão por empresa.

POR QUE ISTO EXISTE. A migração 015 criou a coluna VAZIA de propósito: 37 das 39
empresas com dado tinham anos DIFERENTES entre suas cidades, e não há resposta
automática para qual deles vale para a operadora. A decisão era da Aegea.

Só que a coluna vazia deixa a aba de Empresas sem nada para mostrar, e o pedido
(31/08) é que ela venha preenchida — a informação já existia, município a
município. Então este script a consolida, e o faz com uma regra que dá para
defender em voz alta:

  1. A MODA das cidades da empresa — o ano em que a maioria delas já concorda.
     Não é o maior (que estenderia concessão e inflaria receita) nem o menor
     (que apagaria receita real): é o que menos inventa.
  2. Empate na moda: fica o MAIOR dos empatados. Escolha arbitrária, mas
     determinística — rodar duas vezes dá o mesmo resultado.
  3. Ano implausível é DESCARTADO antes da conta. Há três cidades com 1987 na
     base, que é anterior à própria concessão; tratá-las como voto faria uma
     empresa inteira herdar um ano impossível.
  4. Empresa sem NENHUMA cidade com ano recebe um sorteio entre 2040 e 2050,
     como pedido. A semente é fixa, então o sorteio é reproduzível.

EFEITO COLATERAL QUE É O PONTO: gravar na empresa dispara
`empresa_propaga_concessao` (migração 015), e as cidades dela convergem para o
ano da empresa. É isso que a decisão "a empresa define, a cidade herda" queria
dizer — e é aqui que ela acontece pela primeira vez. As três cidades com 1987
saem corrigidas de tabela.

Idempotente: só escreve em empresa cujo campo está VAZIO ou IMPLAUSÍVEL. O
segundo caso não é preciosismo — havia uma empresa gravada com 1987, e um ano
anterior à concessão passando por "preenchido" é pior que vazio: ele não aparece
como pendência e ainda assim leva o motor a não contar receita nenhuma.
"""

import asyncio
import os
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

#: Antes disto, o ano não é fim de concessão — é erro de carga. O menor valor
#: legítimo na base é 2040; 1987 (três cidades) está muito abaixo de qualquer
#: contrato em vigor.
ANO_MINIMO_PLAUSIVEL = 2030

#: A faixa do sorteio, para empresa sem nenhuma cidade com ano.
FAIXA = (2040, 2050)

#: Semente fixa: o sorteio precisa dar o mesmo resultado em toda máquina, senão
#: dois ambientes de desenvolvimento discordam sobre o horizonte do plano.
SEMENTE = 20260831

HOSTS_PERMITIDOS = ("localhost", "127.0.0.1", "::1", "db", "postgres")

URL = os.environ.get(
    "POSTGRES_URL", "postgresql://otim:otim@localhost:55432/otimizador"
)


def _host(url: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower()


def escolher(anos: list[int], sorteio: random.Random) -> tuple[int, str]:
    """O ano da empresa, e de onde ele veio."""
    validos = [a for a in anos if a >= ANO_MINIMO_PLAUSIVEL]
    if not validos:
        return sorteio.randint(*FAIXA), "sorteado"
    contagem = Counter(validos)
    maior = max(contagem.values())
    # Empate resolvido pelo maior ano — arbitrário, mas determinístico.
    return max(a for a, n in contagem.items() if n == maior), "moda"


async def main() -> None:
    if _host(URL) not in HOSTS_PERMITIDOS and "--forcar" not in sys.argv:
        print(
            f"Recusado: POSTGRES_URL aponta para {_host(URL)!r}, fora da lista "
            f"de hosts locais {HOSTS_PERMITIDOS}.\n"
            "Este script grava fim de concessão consolidado e propaga para os "
            "municípios. Se for mesmo o que você quer, repita com --forcar."
        )
        raise SystemExit(1)

    sorteio = random.Random(SEMENTE)
    con = await asyncpg.connect(URL)
    try:
        linhas = await con.fetch(
            """SELECT e.emp_codigo, e.empresa,
                      array_remove(array_agg(o.data_fim_concessao), NULL) AS anos
                 FROM input.empresa e
                 LEFT JOIN input.cidade_empresa ce ON ce.emp_codigo = e.emp_codigo
                 LEFT JOIN input.cidade_operacional o ON o.cidade_id = ce.cidade_id
                WHERE e.data_fim_concessao IS NULL
                   OR e.data_fim_concessao < $1
                GROUP BY e.emp_codigo, e.empresa
                ORDER BY e.emp_codigo""",
            ANO_MINIMO_PLAUSIVEL,
        )
        if not linhas:
            print("Nada a fazer: todas as empresas já têm fim de concessão.")
            return

        de_moda = sorteados = 0
        for l in linhas:
            ano, origem = escolher(list(l["anos"] or []), sorteio)
            await con.execute(
                "UPDATE input.empresa SET data_fim_concessao = $2 WHERE emp_codigo = $1",
                l["emp_codigo"],
                ano,
            )
            if origem == "moda":
                de_moda += 1
            else:
                sorteados += 1

        print(f"{len(linhas)} empresas preenchidas/reparadas: {de_moda} pela moda das "
              f"cidades, {sorteados} sorteadas em {FAIXA[0]}–{FAIXA[1]}.")

        r = await con.fetchrow(
            """SELECT COUNT(*) FILTER (WHERE data_fim_concessao IS NULL) AS vazias,
                      MIN(data_fim_concessao) AS de, MAX(data_fim_concessao) AS ate
                 FROM input.empresa"""
        )
        print(f"  empresas sem ano: {r['vazias']}  ·  faixa: {r['de']}–{r['ate']}")

        c = await con.fetchrow(
            """SELECT COUNT(*) FILTER (WHERE data_fim_concessao IS NULL) AS vazias,
                      COUNT(*) FILTER (WHERE data_fim_concessao < $1) AS implausiveis
                 FROM input.cidade_operacional""",
            ANO_MINIMO_PLAUSIVEL,
        )
        print(f"  cidades sem ano: {c['vazias']}  ·  com ano implausível: "
              f"{c['implausiveis']} (a propagação corrige)")
    finally:
        await con.close()


if __name__ == "__main__":
    asyncio.run(main())
