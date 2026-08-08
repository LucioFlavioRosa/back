"""O disparo inteiro, com a fila de verdade.

Até aqui `POST /runs` só era exercitado com o Service Bus AUSENTE — sempre 503, e
o caminho feliz nunca rodou. Este smoke usa o emulador oficial do Service Bus
(`docker compose`), então prova o que ninguém tinha provado: a rodada é gravada,
a mensagem chega à fila com o corpo certo, e o `run_id` é a chave de deduplicação.

Exige a pilha de pé e o `dev/seed.sql` aplicado.
"""

import asyncio
import json
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
from azure.servicebus.aio import ServiceBusClient  # noqa: E402

logging.disable(logging.WARNING)
from app.infra import db  # noqa: E402
from main import app  # noqa: E402

falhas: list[str] = []


def ck(nome: str, cond: bool, detalhe: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FALHA'} {nome}{'' if cond else '  <- ' + detalhe}")
    if not cond:
        falhas.append(nome)


async def drenar() -> list[dict]:
    async with ServiceBusClient.from_connection_string(CS) as c:
        async with c.get_queue_receiver("otimizacoes", max_wait_time=5) as r:
            msgs = await r.receive_messages(max_message_count=10, max_wait_time=5)
            out = []
            for m in msgs:
                out.append(json.loads(str(m)))
                await r.complete_message(m)
            return out


async def main() -> None:
    await drenar()  # comeca com a fila limpa
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            # o cadastro do seed tem pendencias; preenche o minimo para a guarda passar
            await db.buscar(
                """UPDATE input.subbacia_operacional SET tempo_arrecadacao=3, tempo_ramp_up=6,
                   vazao_contribuicao=41.2, vazao_contribuicao_industrial=0,
                   potencial_crescimento=0.05 WHERE sub_bacia='b38_1'"""
            )
            await db.buscar(
                "UPDATE input.cidade_operacional SET data_fim_concessao=2049 WHERE cidade_id='c_rio'"
            )

            r = await c.post(
                "/api/runs",
                json={
                    "unidade_id": "u1",
                    "nome": "com fila",
                    "orcamento": {"2026": 60e6, "2027": 50e6},
                    "base_receita": "arrecadada",
                    "usar_cts": True,
                },
            )
            ck("POST /runs aceita quando ha fila", r.status_code == 201, f"{r.status_code} {r.text[:90]}")
            run_id = r.json().get("runId", "")

            msgs = await drenar()
            ck("a mensagem chega na fila", len(msgs) == 1, f"{len(msgs)} mensagem(ns)")
            if msgs:
                m = msgs[0]
                ck(
                    "o corpo carrega run_id, unidade e quem pediu",
                    m.get("run_id") == run_id
                    and m.get("unidade_id") == "u1"
                    and m.get("solicitado_por"),
                    json.dumps(m),
                )

            st = await db.buscar(
                "SELECT status FROM controle.run_status WHERE run_id = $1", run_id
            )
            ck(
                "a rodada fica PENDENTE, e nao ERRO",
                st and st[0]["status"] == "PENDENTE",
                str(st),
            )

            params = await db.buscar(
                "SELECT jsonb_typeof(params) t, params->>'UNIDADE' u"
                " FROM controle.run_request WHERE run_id = $1",
                run_id,
            )
            ck(
                "params gravado como objeto, com a unidade",
                params and params[0]["t"] == "object" and params[0]["u"] == "u1",
                str(params),
            )

            # o retry reusa o mesmo id enquanto nada publicou
            r = await c.post(f"/api/runs/{run_id}/reexecutar")
            ck("reexecutar recusa rodada em voo", r.status_code == 409, f"deu {r.status_code}")

            await db.buscar(
                "UPDATE controle.run_status SET status='ERRO' WHERE run_id=$1", run_id
            )
            r = await c.post(f"/api/runs/{run_id}/reexecutar")
            ck("reexecutar aceita rodada que falhou", r.status_code == 202, f"deu {r.status_code}")
            msgs = await drenar()
            ck("o retry reenfileira o MESMO run_id", len(msgs) == 1 and msgs[0]["run_id"] == run_id, str(msgs))

            await db.buscar(
                "UPDATE controle.run_status SET status='SUCESSO' WHERE run_id=$1", run_id
            )
            r = await c.post(f"/api/runs/{run_id}/reexecutar")
            ck(
                "rodada publicada congela: reexecutar da 409",
                r.status_code == 409 and "publicada" in r.text,
                f"{r.status_code} {r.text[:90]}",
            )

    print("\nFALHAS:", falhas or "nenhuma")
    raise SystemExit(1 if falhas else 0)


asyncio.run(main())
