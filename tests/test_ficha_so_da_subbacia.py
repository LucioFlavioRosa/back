"""AS `*_com_cts` SÓ NA SUB-BACIA, E SÓ DE LEITURA.

A CTS não tem essas colunas, e `COLETA` é a ficha que as duas compartilham:
lê-las na CTS quebraria a leitura por coluna inexistente. E elas não entram no
`PUT`: são medida da base comercial, como o `ticket` é conta do servidor.
"""
from app.dominio import campos
from app.dominio.ficha import exigir_ficha_inteira
from app.infra.repositorios.cadastro import _ficha_coleta

LINHA_SUB = {
    "sub_bacia": "b1",
    **{coluna: 10 for coluna in campos.COLETA},
    **{coluna: 7 for coluna in campos.SO_DA_SUBBACIA},
    "atualizado_em": None,
    "atualizado_por": None,
}
LINHA_CTS = {
    "cts": "c1",
    **{coluna: 10 for coluna in campos.COLETA},
    "atualizado_em": None,
    "atualizado_por": None,
}


def test_as_com_cts_ficam_fora_da_ficha_compartilhada():
    assert not set(campos.SO_DA_SUBBACIA) & set(campos.COLETA)
    assert not set(campos.SO_DA_SUBBACIA.values()) & set(campos.COLETA.values())
    # e fora do contrato do PUT: o corpo inteiro é `CAMPOS_DB`, sem elas
    assert not set(campos.SO_DA_SUBBACIA.values()) & set(campos.CAMPOS_DB)


def test_a_sub_bacia_traz_as_dez_no_bloco_db():
    ficha = _ficha_coleta(LINHA_SUB, "sub_bacia")
    for nome in campos.SO_DA_SUBBACIA.values():
        assert ficha["db"][nome] == "7", nome
    # e o que já estava lá continua
    assert ficha["db"]["ligU"] == "10"
    assert "ligUCts" not in ficha["params"]


def test_a_cts_nao_as_tem_e_a_leitura_nao_quebra():
    ficha = _ficha_coleta(LINHA_CTS, "cts")
    assert not set(campos.SO_DA_SUBBACIA.values()) & set(ficha["db"])


def test_o_put_nao_as_exige():
    """A ficha lida tem de poder ser reenviada — com ou sem as dez. O front manda
    o bloco `db` como o leu, e o servidor as ignora na gravação."""
    corpo = {"db": {c: "1" for c in campos.CAMPOS_DB}, "params": {c: "1" for c in campos.CAMPOS_PARAMS}}
    exigir_ficha_inteira(corpo)          # sem as dez: passa
    corpo["db"].update({v: "1" for v in campos.SO_DA_SUBBACIA.values()})
    exigir_ficha_inteira(corpo)          # com as dez: passa igual
