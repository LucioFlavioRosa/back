"""O MÓDULO DE EXPANSÃO DA ETE NOVA, no cadastro.

Pedido do cliente em 29/09/2026: na ETE nova constrói-se uma quantidade definida de
módulos iniciais e depois expande-se se necessário, e os dois tipos têm vazão e preço
diferentes. `modulos` continua sendo a quantidade inicial; as duas colunas novas
(migração 026) dizem quanto trata e quanto custa CADA módulo de expansão.

Vazias = iguais ao módulo inicial, que é o caso das 639 ETEs do cadastro. É isso que
permite a migração subir sem mexer no número de nenhuma delas.
"""
import io
import pathlib

import pytest

from app.dominio.ficha import ETE, ETE_NUM
from app.dominio.formato import numerico
from app.infra.db import _EXIGIDO
from app.infra.repositorios.cadastro import _COLUNAS_ETE_SQL, _MAPA_ETE
from app.infra.repositorios.pendencias import _ETE, _ETE_NOVA

COLUNAS = ("capacidade_por_modulo_expansao", "capex_por_modulo_expansao")
CAMPOS = ("capExpMod", "capexExpMod")
MIGRACAO = "026_o_modulo_de_expansao_da_ete.sql"


# ------------------------------------------------------------------ a ficha
@pytest.mark.parametrize("campo,coluna", zip(CAMPOS, COLUNAS, strict=True))
def test_a_ficha_da_ete_grava_as_duas_colunas(campo, coluna):
    assert ETE[campo] == coluna


@pytest.mark.parametrize("campo", CAMPOS)
def test_as_duas_sao_NUMERO(campo):
    """Sem entrar em `ETE_NUM`, `numerico` não roda e o driver recebe a string crua
    numa coluna `double precision` — o `PATCH` estoura em vez de gravar."""
    assert campo in ETE_NUM
    assert numerico("260.000,50", f"ete.{campo}") == 260000.50


def test_vazio_continua_sendo_AUSENCIA_e_zero_continua_sendo_zero():
    """A distinção que a regra do motor usa: coluna vazia significa "igual ao módulo
    inicial", e um zero declarado significa "este módulo não custa". Se o cadastro
    transformasse vazio em 0, toda ETE nova passaria a ter expansão de graça."""
    assert numerico("", "ete.capexExpMod") is None
    assert numerico("0", "ete.capexExpMod") == 0.0


# ------------------------------------------------------- leitura e gravação juntas
#
# O de/para da leitura é DERIVADO do da gravação, e estes testes prendem a derivação:
# era ela, escrita duas vezes, que permitia uma coluna existir num sentido só.
@pytest.mark.parametrize("coluna", COLUNAS)
def test_a_leitura_traz_as_duas(coluna):
    assert coluna in _MAPA_ETE
    assert f"e.{coluna}" in _COLUNAS_ETE_SQL


def test_a_LEITURA_e_a_GRAVACAO_falam_das_MESMAS_colunas():
    """Uma coluna só na leitura volta vazia no `PUT` e apaga o que estava no banco;
    uma coluna só na gravação nunca é lida pela tela. Nenhum dos dois dá erro.

    `nova` é a única exceção, e por ser TEXTO: ela não passa por `pt_br` e a ficha a
    monta à parte.
    """
    assert set(_MAPA_ETE) == set(ETE.values()) - {"nova"}
    assert set(_MAPA_ETE.values()) == set(ETE) - {"nova"}


def test_capacidade_ociosa_continua_fora_das_duas():
    """Campo derivado (nominal − vazão de operação) não volta no PUT, como o `ticket`
    da sub-bacia. Derivar o mapa não pode ter trazido ele de carona."""
    assert "capacidade_ociosa" not in _MAPA_ETE
    assert "capacidade_ociosa" not in ETE.values()


# ------------------------------------------------------------------ pendências
@pytest.mark.parametrize("coluna", COLUNAS)
def test_as_duas_NAO_sao_pendencia(coluna):
    """Pendência é campo que FALTA, e estas podem legitimamente ficar vazias.

    Cobrá-las tornaria as 639 ETEs do cadastro incompletas de um dia para o outro e
    travaria as unidades — a barra de completude mediria uma decisão de produto como se
    fosse buraco de cadastro.
    """
    assert coluna not in _ETE
    assert coluna not in _ETE_NOVA


# ------------------------------------------------------------------ a migração
def test_a_migracao_existe_e_cria_as_duas_colunas():
    sql = io.open(pathlib.Path("migracoes") / MIGRACAO, encoding="utf-8").read()
    for coluna in COLUNAS:
        assert f"ADD COLUMN IF NOT EXISTS {coluna}" in sql.replace("      ", " ").replace(
            "  ", " "), coluna
    assert "COMMENT ON COLUMN" in sql, "a coluna precisa dizer o que é, no próprio banco"


def test_o_readyz_recusa_banco_sem_a_migracao():
    """A ficha da ETE passa a ter os dois campos, e o `PUT` os grava pelo mapa `ETE`:
    num banco sem a coluna, quem preencher o preço do módulo de expansão recebe 500 ao
    salvar — e a perda não é do campo novo, é da edição toda.

    O motor tolera a ausência (coluna vazia é o comportamento de sempre); a TELA não.
    """
    exigidas = {(tabela, coluna, migracao) for _esquema, tabela, coluna, migracao in _EXIGIDO}
    assert ("ete_capex", "capex_por_modulo_expansao", MIGRACAO) in exigidas
