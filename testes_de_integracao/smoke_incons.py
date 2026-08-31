"""As inconsistencias de CTS que o `GET /cts` denuncia.

Escrito contra DADO REAL, e nao contra um seed meu: os dois casos conhecidos
(`cts_b2b80_1_3` em uA2 e `cts_c2b12_3_1` em uA3) vieram da planilha, nao de
teste. Por isso o teste NAO fixa ids — ele pergunta ao banco quais deveriam
aparecer e confere que a API disse exatamente aquilo. Fixar os ids faria o teste
falhar no dia em que o cadastro fosse corrigido, que e o dia em que ele deveria
passar mais tranquilo.
"""

import asyncio
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


#: A mesma pergunta das tres consultas, feita de um jeito diferente de proposito:
#: se eu repetisse o SQL do repositorio, o teste concordaria com o erro dele.
ESPERADO = """
SELECT o.cts, 'ficha-sem-no'
  FROM input.cts_operacional o
 WHERE o.cts NOT IN (SELECT componente_sistema_id FROM input.sistema_topologia)
   AND o.cts IN (SELECT cts FROM input.subbacia_cts)
UNION ALL
SELECT t.componente_sistema_id, 'no-sem-ficha'
  FROM input.sistema_topologia t
 WHERE t.componente_sistema_id LIKE 'cts%'
   AND t.componente_sistema_id NOT IN (SELECT cts FROM input.cts_operacional)
UNION ALL
SELECT o.cts, 'sem-par'
  FROM input.cts_operacional o
 WHERE o.cts IN (SELECT componente_sistema_id FROM input.sistema_topologia)
   AND o.cts NOT IN (SELECT cts FROM input.subbacia_cts)
"""


async def main() -> None:
    from app.infra import db

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            # Nao ha `GET /unidades`: a lista sai por regional, que e como o
            # front navega. Percorrer TODAS importa aqui — o teste fecha a conta
            # comparando a uniao das unidades com o banco inteiro.
            unidades = [
                u["id"]
                for r in (await c.get("/api/regionais")).json()
                for u in (await c.get(f"/api/regionais/{r['id']}/unidades")).json()
            ]
            ck("ha unidades para conferir", bool(unidades), "nenhuma")
            if not unidades:
                return

            # O que o banco inteiro tem de quebrado, sem passar pela API.
            todos = {(r["cts"], r["?column?"]) for r in await db.buscar(ESPERADO)}
            print(f"  (banco: {len(todos)} inconsistencia(s) em {len(unidades)} unidades)")

            visto: set[tuple[str, str]] = set()
            for u in unidades:
                d = (await c.get(f"/api/unidades/{u}/cts")).json()
                ck(f"{u}: o payload tem `inconsistencias`", "inconsistencias" in d,
                   str(sorted(d))[:80])
                achados = {(x["id"], x["tipo"]) for x in d.get("inconsistencias", [])}
                ck(f"{u}: nada inventado", achados <= todos, str(sorted(achados - todos)))
                ck(f"{u}: campos completos",
                   all({"tipo", "id", "detalhe"} <= set(x) for x in d.get("inconsistencias", [])),
                   "falta tipo/id/detalhe")
                visto |= achados

                # Uma CTS com ficha mas sem no continua EDITAVEL: some de `ctss`
                # seria pior que denunciar, porque tira do usuario a unica tela
                # onde ele veria o problema.
                for x in d.get("inconsistencias", []):
                    if x["tipo"] == "ficha-sem-no":
                        ck(f"{u}: {x['id']} continua em ctss", x["id"] in d["ctss"],
                           "sumiu da lista editavel")

            # O recorte por unidade nao pode ESCONDER: a uniao das unidades tem de
            # dar o total do banco. Um `JOIN` errado no recorte apareceria aqui
            # como inconsistencia que existe e nunca e mostrada a ninguem.
            ck("nenhuma inconsistencia fica sem dono", visto == todos,
               f"invisiveis: {sorted(todos - visto)}")

    print("\nFALHAS:", falhas or "nenhuma")
    raise SystemExit(1 if falhas else 0)


# RODA COMO SCRIPT, e só como script.
#
# Sem este guarda, importar o arquivo — o que o pytest faz ao COLETAR — dispara
# a bateria inteira contra a API e termina o processo num `SystemExit`. Eles não
# são testes de pytest: são programas que falam com um serviço de pé.
if __name__ == "__main__":
    asyncio.run(main())
