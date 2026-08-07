"""Dois `POST /runs` ao mesmo tempo não podem virar duas execuções.

O que se protege NÃO é "duas rodadas da mesma unidade" — isso é o produto
funcionando, e a tela de histórico existe para comparar cenários. É o **mesmo
pedido** virando duas execuções: duplo clique, retry do navegador, ou duas
pessoas pedindo a mesma coisa.

Exige a pilha de pé (Postgres + Service Bus) e o `dev/seed.sql` aplicado.
"""

import asyncio
import os
import sys

CS = (
    "Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;"
    "SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;"
)
os.environ["POSTGRES_URL"] = "postgresql://otim:otim@localhost:55432/otimizador"
os.environ["SERVICE_BUS_CONN"] = CS
sys.path.insert(0, ".")

import logging  # noqa: E402

import httpx  # noqa: E402

logging.disable(logging.WARNING)
from app.infra import db  # noqa: E402
from main import app  # noqa: E402

falhas: list[str] = []


def ck(nome: str, cond: bool, detalhe: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FALHA'} {nome}{'' if cond else '  <- ' + detalhe}")
    if not cond:
        falhas.append(nome)


PEDIDO = {
    "unidade_id": "u1",
    "orcamento": {"2026": 60e6, "2027": 50e6},
    "base_receita": "arrecadada",
    "usar_cts": True,
}


async def main() -> None:
    async with app.router.lifespan_context(app):
        # o seed tem pendencias; zera para a guarda deixar passar
        await db.buscar(
            """UPDATE input.subbacia_operacional SET tempo_arrecadacao=3, tempo_ramp_up=6,
               vazao_contribuicao=41.2, vazao_contribuicao_industrial=0,
               potencial_crescimento=0.05 WHERE sub_bacia='b38_1'"""
        )
        await db.buscar(
            "UPDATE input.cidade_operacional SET data_fim_concessao=2049 WHERE cidade_id='c_rio'"
        )
        await db.buscar("DELETE FROM controle.run_status")
        await db.buscar("DELETE FROM controle.run_request")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            # 1. DEZ pedidos idênticos ao mesmo tempo — o caso do duplo clique
            rs = await asyncio.gather(*[c.post("/api/runs", json=PEDIDO) for _ in range(10)])
            ids = {r.json().get("runId") for r in rs}
            n = (await db.buscar("SELECT count(*) n FROM controle.run_request"))[0]["n"]
            ck(
                "10 pedidos idênticos simultâneos = 1 rodada",
                len(ids) == 1 and n == 1,
                f"{len(ids)} runId(s), {n} linha(s) no banco",
            )
            ck(
                "só o primeiro responde 201; os demais, 200",
                sorted(r.status_code for r in rs) == [200] * 9 + [201],
                str(sorted(r.status_code for r in rs)),
            )

            # 2. parâmetros DIFERENTES continuam livres — é o uso normal
            outro = {**PEDIDO, "foco_cobertura": 0.0}
            r = await c.post("/api/runs", json=outro)
            n = (await db.buscar("SELECT count(*) n FROM controle.run_request"))[0]["n"]
            ck(
                "cenário diferente da mesma unidade não é bloqueado",
                r.status_code == 201 and n == 2,
                f"{r.status_code}, {n} rodada(s)",
            )

            # 3. o usuário não entra na identidade do pedido
            r = await c.post("/api/runs", json=PEDIDO)
            ck(
                "outra pessoa pedindo o mesmo é levada à rodada existente",
                r.status_code == 200 and r.json()["runId"] in ids,
                f"{r.status_code} {r.text[:80]}",
            )

            # 4. terminada a rodada, o mesmo pedido pode rodar de novo
            await db.buscar("UPDATE controle.run_status SET status='SUCESSO'")
            r = await c.post("/api/runs", json=PEDIDO)
            ck(
                "com nada em voo, o mesmo pedido cria rodada nova",
                r.status_code == 201 and r.json()["runId"] not in ids,
                f"{r.status_code} {r.text[:80]}",
            )

            # 5. unidades diferentes não se serializam entre si
            await db.buscar(
                "INSERT INTO input.unidade_regional (unidade_id, unidade_name, regional_id)"
                " VALUES ('u_par','Paralela','r1') ON CONFLICT DO NOTHING"
            )
            r1, r2 = await asyncio.gather(
                c.post("/api/runs", json=PEDIDO),
                c.post("/api/runs", json={**PEDIDO, "unidade_id": "u_par"}),
            )
            ck(
                "unidade diferente não é bloqueada pelo lock da outra",
                {r1.status_code, r2.status_code} <= {200, 201},
                f"{r1.status_code} / {r2.status_code}",
            )

    print("\nFALHAS:", falhas or "nenhuma")
    raise SystemExit(1 if falhas else 0)


asyncio.run(main())
