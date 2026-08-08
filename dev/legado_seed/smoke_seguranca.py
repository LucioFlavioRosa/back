"""Os buracos que a revisão da escrita encontrou, virados em prova.

Cada bloco reproduz um ataque que FUNCIONAVA e confere que agora não funciona
mais. Roda contra o Postgres do `docker-compose`, com o seed aplicado.
"""

import asyncio
import os
import sys

os.environ["POSTGRES_URL"] = "postgresql://otim:otim@localhost:55432/otimizador"
os.environ["SERVICE_BUS_CONN"] = ""
sys.path.insert(0, ".")

import logging  # noqa: E402

import httpx  # noqa: E402

logging.disable(logging.WARNING)
from app.infra import db  # noqa: E402
from main import app  # noqa: E402

# `params` e `db` viajam INTEIROS (contrato, e agora `_exigir_ficha_inteira`):
# campo vazio vai como string vazia, nunca ausente. Este helper monta a ficha
# completa para o teste nao repetir as 20 chaves em cada chamada — que e
# exatamente o que o `fichas.ts` do front faz num lugar so.
_DB = ["arr", "arrInd", "ecoA", "ecoN", "ecoU", "fat", "fatInd",
       "ligA", "ligAInd", "ligN", "ligU", "ligUInd"]
_PARAMS = ["preco", "tarr", "ramp", "vaz", "vazInd", "pot", "popU", "popA"]


def ficha(params=None, db=None, **resto):
    corpo = {"params": {**{k: "" for k in _PARAMS}, **(params or {})},
             "db": {**{k: "" for k in _DB}, **(db or {})}}
    corpo.update(resto)
    return corpo


SUB = "b38_1"  # pertence a u1


async def main() -> None:
    falhas: list[str] = []

    def checar(nome: str, condicao: bool, detalhe: str = "") -> None:
        print(f"  {'ok  ' if condicao else 'FALHA'} {nome}{'' if condicao else '  <- ' + detalhe}")
        if not condicao:
            falhas.append(nome)

    # A trilha e APPEND-ONLY: rodada duas vezes seguidas sem limpar, a segunda
    # encontra o historico da primeira e as checagens de data/sequencia falham por
    # sujeira, nao por bug. Limpar aqui e mais honesto que fingir isolamento.
    async with app.router.lifespan_context(app):
        await db.buscar("DELETE FROM input.override")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            # 1. gravar na ficha de OUTRA unidade so trocando o id da URL
            await db.buscar(
                "INSERT INTO input.unidade_regional (unidade_id, unidade_name, regional_id)"
                " VALUES ('u_outra','Outra','r1') ON CONFLICT DO NOTHING"
            )
            r = await c.put(
                f"/api/unidades/u_outra/sub-bacias/{SUB}",
                json=ficha(**{"params": {"preco": "9.999,00"}, "overrides": []}),
            )
            checar("ficha de outra unidade e recusada", r.status_code == 404, f"deu {r.status_code}")

            # 2. PUT em ficha inexistente nao pode criar linha orfa
            r = await c.put(
                "/api/unidades/u1/sub-bacias/nao_existe",
                json=ficha(**{"params": {"preco": "1,00"}, "overrides": []}),
            )
            orfa = await db.buscar(
                "SELECT count(*) n FROM input.subbacia_operacional WHERE sub_bacia='nao_existe'"
            )
            checar(
                "PUT em ficha inexistente da 404 e nao cria",
                r.status_code == 404 and orfa[0]["n"] == 0,
                f"status {r.status_code}, linhas {orfa[0]['n']}",
            )

            # 3. autor forjado no corpo tem de ser ignorado
            await c.put(
                f"/api/unidades/u1/sub-bacias/{SUB}",
                json=ficha(**{
                    "params": {"preco": "1.900,00"},
                    "overrides": [
                        {
                            "campo": "ligU",
                            "valorAntigo": "300",
                            "valorNovo": "351",
                            "autor": "forjado@corp",
                        }
                    ],
                }),
            )
            autores = await db.buscar("SELECT DISTINCT autor FROM input.override")
            checar(
                "autor do corpo e ignorado",
                all(a["autor"] != "forjado@corp" for a in autores),
                str([a["autor"] for a in autores]),
            )

            # 4. a trilha nao pode reescrever a data de um fato antigo
            await db.buscar(
                "UPDATE input.override SET gravado_em = '2026-07-01'::timestamptz"
                " WHERE campo = 'ligU'"
            )
            antes = await db.buscar(
                "SELECT count(*) n FROM input.override WHERE gravado_em < '2026-08-01'"
            )
            await c.put(
                f"/api/unidades/u1/sub-bacias/{SUB}",
                json=ficha(**{
                    "params": {"preco": "1.900,00"},
                    "overrides": [
                        {"campo": "ligU", "valorAntigo": "300", "valorNovo": "351"}
                    ],
                }),
            )
            depois = await db.buscar(
                "SELECT count(*) n FROM input.override WHERE gravado_em < '2026-08-01'"
            )
            checar(
                "correcao antiga mantem a data original",
                antes[0]["n"] == depois[0]["n"] == 1,
                f"{antes[0]['n']} -> {depois[0]['n']}",
            )

            # 5. PUT identico repetido nao acumula linha na trilha
            n1 = (await db.buscar("SELECT count(*) n FROM input.override"))[0]["n"]
            for _ in range(3):
                await c.put(
                    f"/api/unidades/u1/sub-bacias/{SUB}",
                    json=ficha(**{
                        "params": {"preco": "1.900,00"},
                        "overrides": [
                            {"campo": "ligU", "valorAntigo": "300", "valorNovo": "351"}
                        ],
                    }),
                )
            n2 = (await db.buscar("SELECT count(*) n FROM input.override"))[0]["n"]
            checar("PUT repetido nao duplica a trilha", n1 == n2, f"{n1} -> {n2}")

            # 6. mudanca de valor GERA linha nova, sem apagar a anterior
            await c.put(
                f"/api/unidades/u1/sub-bacias/{SUB}",
                json=ficha(**{
                    "params": {"preco": "1.900,00"},
                    "overrides": [
                        {"campo": "ligU", "valorAntigo": "300", "valorNovo": "400"}
                    ],
                }),
            )
            hist = await db.buscar(
                "SELECT valor_novo FROM input.override WHERE campo='ligU' ORDER BY override_id"
            )
            checar(
                "mudanca acrescenta e preserva o historico",
                [h["valor_novo"] for h in hist] == ["351", "400"],
                str([h["valor_novo"] for h in hist]),
            )

            # 7. numero pt-BR chega convertido, e nao como texto
            preco = await db.buscar(
                f"SELECT preco_por_ligacao p FROM input.subbacia_operacional WHERE sub_bacia='{SUB}'"
            )
            checar("string pt-BR vira numero", preco[0]["p"] == 1900.0, str(preco[0]["p"]))

            # 8. obrasOverride no formato REAL do front: Record<indice, Partial>
            r = await c.put(
                f"/api/unidades/u1/sub-bacias/{SUB}",
                json=ficha(**{
                    "params": {"preco": "1.900,00"},
                    "obrasOverride": {"0": {"qtd": "100", "preco": "2.000,00"}},
                    "overrides": [],
                }),
            )
            obras = await db.buscar(
                "SELECT componente, quantidade, preco_unitario, capex"
                f" FROM input.componentes_subbacias_capex WHERE sub_bacia='{SUB}'"
                " ORDER BY componente"
            )
            checar(
                "obrasOverride indexado grava as 5 obras",
                r.status_code == 200 and len(obras) == 5,
                f"status {r.status_code}, {len(obras)} obras",
            )
            lig = next((o for o in obras if "iga" in o["componente"]), None)
            checar(
                "override por indice aplica sobre a base",
                lig is not None and lig["quantidade"] == 100 and lig["capex"] == 200000,
                str(dict(lig)) if lig else "sem ligacao",
            )

            # 9. sem token, em modo auth, nao se cria nem apaga CTS
            from app.config import config

            cfg = config()
            cfg.entra_tenant_id, cfg.entra_audience = "t", "a"
            try:
                r1 = await c.post(
                    "/api/unidades/u1/cts", json={"subId": SUB, "cts": {"id": "cts_x"}}
                )
                r2 = await c.delete("/api/unidades/u1/cts/cts_x")
                checar(
                    "POST/DELETE de CTS exigem token",
                    r1.status_code == 401 and r2.status_code == 401,
                    f"POST {r1.status_code}, DELETE {r2.status_code}",
                )
            finally:
                cfg.entra_tenant_id, cfg.entra_audience = "", ""

    print("\nFALHAS:", falhas or "nenhuma")
    raise SystemExit(1 if falhas else 0)


asyncio.run(main())
