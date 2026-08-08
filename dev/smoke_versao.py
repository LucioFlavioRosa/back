"""O ciclo da `versao` de ponta a ponta, contra a API de verdade.

Este arquivo existe por causa de um erro especifico: o 409 foi construido no
backend, o front ganhou um teste de conflito que passava — e a protecao NUNCA
disparou em producao, porque o front montava a ficha sem `versao` e o teste
mockava a resposta 409 em vez de conferir o que o PUT manda. Duas coisas certas
de cada lado somando zero no meio.

Por isso aqui nada e mockado. O roteiro e o do usuario real:

  1. le a ficha            -> guarda a versao que veio
  2. salva ALTERADA        -> 200, e a resposta traz a versao NOVA
  3. salva com a VELHA     -> 409 (o caso de duas pessoas na mesma ficha)
  4. salva com a NOVA      -> 200 (o ciclo fecha; sem isso o segundo salvamento
                              seguido bateria contra a propria alteracao)
  5. salva SEM versao      -> 200 (script de operacao continua funcionando)

O passo 2 precisa ALTERAR alguma coisa. A versao e o hash do conteudo, entao
regravar a ficha identica nao muda a versao — e nao muda de proposito: salvar
duas vezes o mesmo texto nao e conflito com ninguem. A primeira escrita deste
teste regravava sem alterar e concluia que a protecao estava quebrada.

O passo 4 e o que mais importa e o menos obvio: um 409 que dispara sempre depois
do primeiro salvamento seria pior que nenhum 409, porque o usuario aprenderia a
ignora-lo.

Descobre unidade e ficha pela propria API — fixar ids fez outros smokes
quebrarem quando o dado real substituiu o seed.
"""

import asyncio
import copy
import os
import sys

os.environ.setdefault("POSTGRES_URL", "postgresql://otim:otim@localhost:55432/otimizador")
os.environ.setdefault("SERVICE_BUS_CONN", "")
sys.path.insert(0, ".")

import logging  # noqa: E402

import httpx  # noqa: E402

logging.disable(logging.WARNING)
from main import app  # noqa: E402

falhas: list[str] = []


def ck(nome: str, cond: bool, detalhe: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FALHA'} {nome}{'' if cond else '  <- ' + detalhe}")
    if not cond:
        falhas.append(nome)


async def main() -> None:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60
        ) as c:
            reg = (await c.get("/api/regionais")).json()
            u = (await c.get(f"/api/regionais/{reg[0]['id']}/unidades")).json()[0]["id"]
            subs = (await c.get(f"/api/unidades/{u}/sub-bacias")).json()["subs"]
            sid, ficha = next(iter(subs.items()))
            print(f"  (unidade {u}, sub-bacia {sid})")

            rota = f"/api/unidades/{u}/sub-bacias/{sid}"
            v0 = ficha.get("versao")
            ck("o GET entrega uma versao", bool(v0), repr(v0))

            # O corpo e o que o FRONT manda: ficha inteira + versao no topo.
            def corpo(versao, params=None):
                c_ = {
                    "params": params or copy.deepcopy(ficha["params"]),
                    "db": copy.deepcopy(ficha["db"]),
                    "obrasOverride": copy.deepcopy(ficha["obrasOverride"]),
                    "overrides": [],
                }
                if versao is not None:
                    c_["versao"] = versao
                return c_

            # Altera um campo para a versao mudar de verdade.
            alterado = copy.deepcopy(ficha["params"])
            alterado["pot"] = "9" if alterado.get("pot") != "9" else "8"

            r1 = await c.put(rota, json=corpo(v0, alterado))
            ck("salvar com a versao lida passa", r1.status_code == 200, str(r1.status_code))
            v1 = (r1.json() or {}).get("versao")
            ck("o PUT devolve a versao nova", bool(v1), repr(v1))
            ck("a versao MUDOU depois de alterar a ficha", v1 != v0, f"{v0} == {v1}")

            r2 = await c.put(rota, json=corpo(v0, alterado))
            ck(
                "salvar de novo com a versao VELHA da 409",
                r2.status_code == 409,
                f"{r2.status_code} — a protecao nao disparou",
            )

            r3 = await c.put(rota, json=corpo(v1, alterado))
            ck(
                "salvar com a versao que o PUT devolveu passa",
                r3.status_code == 200,
                f"{r3.status_code} — o ciclo nao fecha: o 2o salvamento seguido "
                "bateria contra a propria alteracao",
            )

            r4 = await c.put(rota, json=corpo(None, alterado))
            ck(
                "sem versao continua passando (script de operacao)",
                r4.status_code == 200,
                str(r4.status_code),
            )

            # Devolve a ficha ao estado original: este smoke roda contra o dado
            # real, e deixar `pot=9` gravado seria o teste sujando o cadastro.
            atual = (await c.get(f"/api/unidades/{u}/sub-bacias")).json()["subs"][sid]
            rv = await c.put(rota, json=corpo(atual.get("versao")))
            ck("restaura a ficha original", rv.status_code == 200, str(rv.status_code))

            fim = (await c.get(f"/api/unidades/{u}/sub-bacias")).json()["subs"][sid]
            ck(
                "a ficha voltou ao que era",
                fim["params"] == ficha["params"] and fim["db"] == ficha["db"],
                f"pot={fim['params'].get('pot')} (era {ficha['params'].get('pot')})",
            )

    print("\nFALHAS:", falhas or "nenhuma")
    raise SystemExit(1 if falhas else 0)


asyncio.run(main())
