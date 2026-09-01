"""O que este teste protege: decimal não entra em coluna inteira, e a lista que
diz quais campos são inteiros não envelhece calada.

O defeito que originou isto: `tarr: "3,7"` era aceito com 200, o Postgres gravava
`3`, e `input.override` registrava `3,7` — auditoria afirmando um número que a
coluna nunca teve. Como o gravado nunca alcançava o enviado, cada novo
salvamento do MESMO valor gerava outra linha de trilha.

São duas defesas, e as duas precisam existir:

  `INTEIROS`          recusa o decimal antes de gravar (422)
  `RETURNING` no diff  a trilha compara o que o BANCO guardou, não o que chegou

A segunda continua valendo mesmo com a primeira: ela cobre qualquer coerção do
banco, inclusive as que ninguém mapeou.
"""

import os

import pytest


from app.dominio.campos import COLETA
from app.dominio.erros import ValorInvalido
from app.dominio.ficha import ETE, OBRA
from app.dominio.formato import INTEIROS, numerico

# ---------------------------------------------------------------- comportamento

DECIMAIS_RECUSADOS = [
    ("tarr", "3,7"),
    ("ramp", "5,4"),
    ("ligU", "1.000,6"),
    ("ecoA", "250,4"),
    ("obra.dur", "2,5"),
    ("obra.anoObrig", "2030,5"),
    ("ete.modulos", "1,5"),
    ("empresa.fim", "2045,9"),
    ("meta.ano", "2030,2"),
]


@pytest.mark.parametrize("campo,valor", DECIMAIS_RECUSADOS)
def test_decimal_em_campo_inteiro_e_recusado(campo, valor):
    with pytest.raises(ValorInvalido) as e:
        numerico(valor, campo)
    assert campo in str(e.value), "a mensagem tem de dizer QUAL campo"


@pytest.mark.parametrize("campo,valor,esperado", [
    ("tarr", "3", 3),
    ("ligU", "1.000", 1000),
    ("obra.dur", "2", 2),
    ("meta.ano", "2030", 2030),
    # `,0` é decimal na digitação e inteiro no valor: recusá-lo seria pedantismo,
    # e a tela pode muito bem mandar assim.
    ("tarr", "3,0", 3),
])
def test_inteiro_escrito_de_varias_formas_passa(campo, valor, esperado):
    assert numerico(valor, campo) == esperado


@pytest.mark.parametrize("campo,valor", [
    ("preco", "1.234,56"),
    ("obra.qtd", "2.472,6"),
    ("obra.wacc", "0,091"),
    ("ete.capexMod", "484.734,61"),
    ("meta.pct", "53,5"),
])
def test_campo_decimal_continua_aceitando_decimal(campo, valor):
    """A recusa vale SÓ para coluna inteira — o resto do cadastro é decimal."""
    assert isinstance(numerico(valor, campo), float)


def test_vazio_continua_sendo_ausencia():
    """Campo em branco é ausência, e não zero — vale também para campo inteiro."""
    assert numerico("", "tarr") is None
    assert numerico(None, "meta.ano") is None


# ---------------------------------------------------------------------- guarda

def _tipos(tabela: str) -> dict[str, str]:
    import asyncio

    from app.infra import db

    async def ler():
        await db.abrir_pool()
        try:
            linhas = await db.buscar(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'input' AND table_name = $1",
                tabela,
            )
        finally:
            await db.fechar_pool()
        return {l["column_name"]: l["data_type"] for l in linhas}

    return asyncio.run(ler())


def _banco_disponivel() -> bool:
    return bool(os.environ.get("POSTGRES_URL", "").endswith("/otimizador"))


INTEIRO = {"integer", "bigint", "smallint"}

#: Campo -> (tabela, coluna). É o de-para que `INTEIROS` afirma conhecer, e o
#: teste abaixo confere os DOIS sentidos contra o banco.
DE_PARA = {
    **{campo: ("subbacia_operacional", coluna) for coluna, campo in COLETA.items()},
    # OS NOMES SAO OS DO BANCO, com underscore: `componentes_subbacias_capex` e
    # `ete_capex`. Escritos colados, `_tipos` devolve `{}` — a tabela nao existe
    # —, nenhum campo de obra ou de ETE entra em `inteiros_no_banco`, e o teste
    # reprova por "sobrando" mesmo com o codigo certo. Ele so nao acusava porque
    # roda pulado sem `POSTGRES_URL`.
    **{f"obra.{campo}": ("componentes_subbacias_capex", coluna)
       for campo, coluna in OBRA.items()},
    **{f"ete.{campo}": ("ete_capex", coluna) for campo, coluna in ETE.items()},
    "empresa.fim": ("empresa", "data_fim_concessao"),
    "meta.ano": ("metas_cobertura", "ano"),
}


@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real (POSTGRES_URL de teste)")
def test_a_lista_de_inteiros_descreve_o_banco():
    """Nos DOIS sentidos: nada a mais na lista, nada a menos.

    Uma coluna que vire inteira sem entrar aqui volta a aceitar decimal em
    silêncio; um campo que deixe de ser inteiro e continue na lista passa a
    recusar dado legítimo. Os dois casos são invisíveis sem este teste.
    """
    tabelas = {t for t, _ in DE_PARA.values()}
    tipos = {t: _tipos(t) for t in tabelas}

    inteiros_no_banco = {
        campo
        for campo, (tabela, coluna) in DE_PARA.items()
        if tipos[tabela].get(coluna) in INTEIRO
    }

    faltando = inteiros_no_banco - INTEIROS
    sobrando = INTEIROS - inteiros_no_banco
    assert not faltando, (
        f"colunas inteiras sem proteção — decimal nelas é arredondado em silêncio: "
        f"{sorted(faltando)}"
    )
    assert not sobrando, (
        f"campos em `INTEIROS` que NÃO são inteiros no banco — estão recusando "
        f"dado válido: {sorted(sobrando)}"
    )
