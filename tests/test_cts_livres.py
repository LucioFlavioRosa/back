"""AS CTS AINDA FORA DE SISTEMA — a ficha existe antes de o sistema ser decidido.

A origem traz a ficha e as obras de cada CTS; o sistema em que ela entra é
decisão da Regional, e vem depois. A planilha do cadastro traz essas fichas
para serem preenchidas ANTES dessa decisão, e o servidor as serve
(`GET /cts?incluirLivres=1`) e aceita a gravação delas (`exigir_dona` conhece a
unidade da CTS livre pela cidade — o mesmo caminho por que a hierarquia a lista
em `semSistema`).

Os testes de banco rodam contra o banco real e pulam sem `POSTGRES_URL`, como
os de `test_obras_do_banco.py`.
"""

import asyncio
import inspect
import os

import pytest

from app.api import cadastro as rotas
from app.infra.repositorios import cadastro, cadastro_escrita
from app.infra.repositorios.cadastro_escrita import FichaDeOutraUnidade


def test_a_rota_das_cts_aceita_incluir_livres_e_nao_as_traz_por_padrao():
    """Opt-in: o front original conta o que tem a preencher pelas colocadas."""
    parametro = inspect.signature(rotas.cts).parameters["incluirLivres"]
    assert parametro.default is False
    assert inspect.signature(cadastro.cts).parameters["incluir_livres"].default is False


def test_a_dona_da_cts_e_o_sistema_e_so_sem_ele_a_cidade():
    """O `COALESCE` põe o sistema antes da cidade: colocada num sistema da
    unidade A com cidade da B, a CTS continua sendo da A."""
    sql = cadastro_escrita._DONO["cts"]
    assert "COALESCE" in sql
    assert sql.index("sistema_topologia") < sql.index("o.cidade_id")
    assert "e_macrorregiao" in sql, "a linha da macrorregião não passa pela cidade"


def _banco_disponivel() -> bool:
    return bool(os.environ.get("POSTGRES_URL", "").endswith("/otimizador"))


def _no_banco(corrotina):
    async def rodar():
        from app.infra import db

        await db.abrir_pool()
        try:
            return await corrotina()
        finally:
            await db.fechar_pool()

    return asyncio.run(rodar())


async def _unidade_com_livres():
    """Uma unidade SEM macrorregião que tenha CTS fora de sistema, e uma delas."""
    from app.infra import db

    linhas = await db.buscar(
        """SELECT u.unidade_id, o.cts
             FROM input.unidade_regional u
             JOIN input.empresa e USING (unidade_id)
             JOIN input.cidade_empresa c USING (emp_codigo)
             JOIN input.cts_operacional o ON o.cidade_id = c.cidade_id
             JOIN input.sistema_topologia t ON t.componente_sistema_id = o.cts
            WHERE t.sistema_id IS NULL
              AND NOT coalesce(o.e_macrorregiao, false)
              AND NOT u.usa_macrorregiao_cts
            ORDER BY u.unidade_id, o.cts
            LIMIT 1"""
    )
    return (linhas[0]["unidade_id"], linhas[0]["cts"]) if linhas else (None, None)


@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real (POSTGRES_URL de teste)")
def test_com_incluir_livres_as_fichas_fora_de_sistema_chegam_com_sistema_vazio():
    async def medir():
        unidade, cts = await _unidade_com_livres()
        if unidade is None:
            pytest.skip("o banco não tem CTS fora de sistema numa unidade sem macrorregião")
        colocadas = await cadastro.cts(unidade)
        todas = await cadastro.cts(unidade, incluir_livres=True)
        return unidade, cts, colocadas["ctss"], todas["ctss"]

    unidade, cts, colocadas, todas = _no_banco(medir)
    assert cts not in colocadas, "sem a flag, a CTS livre não vem — é o padrão da rota"
    assert all(f["sisId"] for f in colocadas.values())
    assert cts in todas
    livre = todas[cts]
    assert livre["sisId"] == "" and livre["sistema"] == "" and livre["jusante"] == ""
    assert livre["nome"], "o nome vem da topologia, como o das colocadas"
    assert isinstance(livre["obrasOverride"], dict)
    # as colocadas continuam iguais: a flag só acrescenta
    assert all(todas[k] == v for k, v in colocadas.items())


@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real (POSTGRES_URL de teste)")
def test_a_cts_livre_pertence_a_unidade_da_cidade_dela_e_a_nenhuma_outra():
    async def medir():
        unidade, cts = await _unidade_com_livres()
        if unidade is None:
            pytest.skip("o banco não tem CTS fora de sistema numa unidade sem macrorregião")
        from app.infra import db

        outra = await db.buscar_um(
            "SELECT unidade_id FROM input.unidade_regional WHERE unidade_id <> $1 LIMIT 1", unidade
        )
        await cadastro_escrita.exigir_dona("cts", cts, unidade)  # não levanta
        recusou = False
        try:
            await cadastro_escrita.exigir_dona("cts", cts, outra["unidade_id"])
        except FichaDeOutraUnidade:
            recusou = True
        return recusou

    assert _no_banco(medir) is True


@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real (POSTGRES_URL de teste)")
def test_com_a_macrorregiao_marcada_as_livres_sao_as_macrorregioes_ja_somadas():
    """A ficha da macrorregião livre é montada como nascerá na colocação: `db`
    somado dos coletores, `params` e as quatro obras em branco, membros junto.
    O teste marca a unidade direto no banco e desmarca no fim — é leitura, e a
    regra de "nenhum sistema com duas CTS" é da escrita."""

    async def medir():
        from app.infra import db

        unidade = await db.buscar_um(
            """SELECT u.unidade_id
                 FROM input.unidade_regional u
                WHERE NOT u.usa_macrorregiao_cts
                  AND EXISTS (SELECT 1 FROM input.empresa e
                                JOIN input.cidade_empresa c USING (emp_codigo)
                                JOIN input.cts_operacional o ON o.cidade_id = c.cidade_id
                               WHERE e.unidade_id = u.unidade_id
                                 AND o.sistema_cts IS NOT NULL AND NOT o.e_macrorregiao)
                ORDER BY u.unidade_id LIMIT 1"""
        )
        if unidade is None:
            pytest.skip("o banco não tem unidade com coletores em macrorregião")
        uid = unidade["unidade_id"]
        await db.buscar("UPDATE input.unidade_regional SET usa_macrorregiao_cts = true WHERE unidade_id = $1", uid)
        try:
            livres = await cadastro._macrorregioes_livres(uid)
            todas = await cadastro.cts(uid, incluir_livres=True)
            sem_flag = await cadastro.cts(uid)
        finally:
            await db.buscar("UPDATE input.unidade_regional SET usa_macrorregiao_cts = false WHERE unidade_id = $1", uid)
        return livres, todas["ctss"], sem_flag["ctss"]

    livres, todas, sem_flag = _no_banco(medir)
    assert livres, "sem macrorregião livre não há o que provar"
    for m in livres:
        assert m["id"] not in sem_flag
        f = todas[m["id"]]
        assert f["sisId"] == "" and f["sistemaCts"] == m["nome"]
        assert f["membros"], "a soma tem de dizer de quem veio"
        assert all(v == "" for v in f["params"].values())
        assert sorted(f["obrasOverride"]) == ["0", "1", "2", "3"]
        assert all(o["qtd"] == "" and o["nome"] for o in f["obrasOverride"].values())
    # a soma confere com os membros: ligações atuais da ficha = soma das dos membros
    m = livres[0]
    f = todas[m["id"]]
    soma = sum(float(x["ligA"].replace(".", "").replace(",", ".") or 0) for x in f["membros"])
    assert float(f["db"]["ligA"].replace(".", "").replace(",", ".") or 0) == soma
