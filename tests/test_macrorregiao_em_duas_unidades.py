"""O MESMO NOME DE MACRORREGIÃO EM DUAS UNIDADES — o caso que o engenheiro achou.

Duas unidades, cada uma com coletores cujo `sistema_cts` é "Sarapuí". Cada uma
coloca a sua macrorregião num sistema seu. Com o nome como id, a segunda
encontrava a linha da primeira e ouvia "pertence a outra unidade"; com o id
composto (nome|empresa|unidade) são duas linhas, e as duas colocações passam.

Roda contra o banco real e pula sem `POSTGRES_URL`, como `test_cts_livres.py`.
Fabrica as duas unidades com ids `zz_teste_*` e as apaga no fim — inclusive o
que a colocação criou (a linha da macrorregião, as obras dela, a topologia).
"""

import asyncio
import os

import pytest

from app.dominio import macrorregiao_cts


def _banco_disponivel() -> bool:
    return bool(os.environ.get("POSTGRES_URL", "").endswith("/otimizador"))


P = "zz_teste_"
REGIONAL = P + "reg"
DIRETORIA = P + "dir"


async def _fabricar(con, sufixo: str) -> dict[str, str]:
    """Uma unidade com uma empresa, uma cidade, um sistema e dois coletores da
    macrorregião "Sarapuí" — tudo com ids `zz_teste_<sufixo>_*`."""
    u, e, c, s = (P + sufixo + x for x in ("_uni", "_emp", "_cid", "_sis"))
    await con.execute(
        "INSERT INTO input.unidade_regional (unidade_id, unidade_name, regional_id, diretoria_id, usa_macrorregiao_cts) "
        "VALUES ($1, $1, $2, $3, true)", u, REGIONAL, DIRETORIA)
    await con.execute("INSERT INTO input.empresa (emp_codigo, unidade_id) VALUES ($1, $2)", e, u)
    await con.execute("INSERT INTO input.cidade (cidade_id, cidade_name) VALUES ($1, $1)", c)
    await con.execute("INSERT INTO input.cidade_empresa (cidade_id, emp_codigo) VALUES ($1, $2)", c, e)
    await con.execute("INSERT INTO input.sistema (sistema_id, sistema_name) VALUES ($1, $1)", s)
    await con.execute("INSERT INTO input.cidade_sistema (sistema_id, sistema_name, cidade_id) VALUES ($1, $1, $2)", s, c)
    for n in (1, 2):
        cts = f"{P}{sufixo}_cts{n}"
        await con.execute(
            "INSERT INTO input.cts_operacional (cts, cidade_id, sistema_cts, e_macrorregiao, universo_ligacoes, ligacoes_atuais) "
            "VALUES ($1, $2, 'Sarapuí', false, $3, $4)", cts, c, 100 * n, 40 * n)
        await con.execute(
            "INSERT INTO input.sistema_topologia (componente_sistema_id, componente_sistema_nome) VALUES ($1, $1)", cts)
    return {"unidade": u, "empresa": e, "cidade": c, "sistema": s}


async def _apagar(con) -> None:
    like = P + "%"
    for sql in (
        "DELETE FROM input.override WHERE ficha_id LIKE $1 OR unidade_id LIKE $1",
        "DELETE FROM input.componentes_cts_capex WHERE cts LIKE $1 OR cts LIKE 'Sarapuí|' || $1",
        "DELETE FROM input.sistema_topologia WHERE componente_sistema_id LIKE $1 OR componente_sistema_id LIKE 'Sarapuí|' || $1",
        "DELETE FROM input.cts_operacional WHERE cts LIKE $1 OR cts LIKE 'Sarapuí|' || $1",
        "DELETE FROM input.cidade_sistema WHERE sistema_id LIKE $1",
        "DELETE FROM input.sistema WHERE sistema_id LIKE $1",
        "DELETE FROM input.cidade_empresa WHERE emp_codigo LIKE $1",
        "DELETE FROM input.cidade WHERE cidade_id LIKE $1",
        "DELETE FROM input.empresa WHERE emp_codigo LIKE $1",
        "DELETE FROM input.unidade_regional WHERE unidade_id LIKE $1",
        "DELETE FROM input.diretoria WHERE diretoria_id LIKE $1",
        "DELETE FROM input.regional WHERE regional_id LIKE $1",
    ):
        await con.execute(sql, like)


def _no_banco(corrotina):
    async def rodar():
        from app.infra import db

        await db.abrir_pool()
        try:
            return await corrotina()
        finally:
            await db.fechar_pool()

    return asyncio.run(rodar())


@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real (POSTGRES_URL de teste)")
def test_duas_unidades_colocam_cada_uma_a_sua_macrorregiao_de_mesmo_nome():
    from app.infra import db
    from app.infra.repositorios import cadastro, cadastro_escrita

    async def cenario():
        async with db.transacao() as con:
            await _apagar(con)
            await con.execute("INSERT INTO input.regional (regional_id) VALUES ($1)", REGIONAL)
            await con.execute("INSERT INTO input.diretoria (diretoria_id, regional_id) VALUES ($1, $2)", DIRETORIA, REGIONAL)
            a = await _fabricar(con, "a")
            b = await _fabricar(con, "b")
        try:
            # 1. A tela de cada unidade oferece a SUA "Sarapuí", com id composto e nome puro.
            livres_a = await cadastro._macrorregioes_livres(a["unidade"])
            livres_b = await cadastro._macrorregioes_livres(b["unidade"])
            id_a = macrorregiao_cts.id_da_macrorregiao("Sarapuí", a["empresa"], a["unidade"])
            id_b = macrorregiao_cts.id_da_macrorregiao("Sarapuí", b["empresa"], b["unidade"])
            assert [m["id"] for m in livres_a] == [id_a] and livres_a[0]["nome"] == "Sarapuí"
            assert [m["id"] for m in livres_b] == [id_b] and livres_b[0]["nome"] == "Sarapuí"

            # 2. As duas colocam — a segunda NÃO ouve "pertence a outra unidade".
            for uni, id_, sis in ((a, id_a, a["sistema"]), (b, id_b, b["sistema"])):
                await cadastro_escrita.salvar_topologia(
                    unidade_id=uni["unidade"], componente_id=id_,
                    corpo={"sisId": sis, "jusante": ""}, autor="teste")

            # 3. Duas linhas, cada uma com a soma dos SEUS coletores (100+200 = 300).
            linhas = await db.buscar(
                "SELECT o.cts, o.universo_ligacoes, t.sistema_id, t.componente_sistema_nome "
                "FROM input.cts_operacional o JOIN input.sistema_topologia t ON t.componente_sistema_id = o.cts "
                "WHERE o.e_macrorregiao AND o.cts LIKE 'Sarapuí|' || $1 ORDER BY o.cts", P + "%")
            assert {l["cts"] for l in linhas} == {id_a, id_b}
            assert all(l["universo_ligacoes"] == 300 for l in linhas)
            assert {l["sistema_id"] for l in linhas} == {a["sistema"], b["sistema"]}
            # E o rótulo que a tela mostra é o nome, não o id.
            assert all(l["componente_sistema_nome"] == "Sarapuí" for l in linhas)

            # 4. Colocada, some da lista de livres de cada uma — e só dela.
            assert await cadastro._macrorregioes_livres(a["unidade"]) == []
            assert await cadastro._macrorregioes_livres(b["unidade"]) == []

            # 5. O id de OUTRA unidade não entra aqui, nem com o sistema certo.
            with pytest.raises(Exception) as erro:
                await cadastro_escrita.salvar_topologia(
                    unidade_id=a["unidade"], componente_id=id_b,
                    corpo={"sisId": a["sistema"], "jusante": ""}, autor="teste")
            assert "unidade" in str(erro.value)
        finally:
            async with db.transacao() as con:
                await _apagar(con)

    _no_banco(cenario)
