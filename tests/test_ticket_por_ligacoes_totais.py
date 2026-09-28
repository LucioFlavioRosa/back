"""OS TICKETS SAEM DAS LIGAÇÕES TOTAIS, E SÃO DOIS.

Defeito relatado pelo dono do produto em 28/09/2026: o ticket vinha de
`receita ÷ ligacoes_atuais`. As duas pontas da divisão têm escopos diferentes —
a receita é a de ÁGUA da sub-bacia inteira (todas as ligações que faturam água,
o `QTD_LIGACOES_TOTAL` da origem) e `ligacoes_atuais` é a base JÁ ATENDIDA com
esgoto. Dividir uma pela outra inflava o ticket por 1/cobertura.

E são DOIS porque a rodada escolhe a base de receita (`BASE_RECEITA`) e a tela
do Cadastro não conhece essa escolha: `ticket` é o da arrecadada — o padrão da
rodada e a chave que o front original já lê — e `ticketFat` o da faturada.

O motor tem a mesma correção, nos dois lugares em que deriva o ticket
(`tests/test_ticket_por_ligacoes_totais.py`, no repositório do motor).
"""
from app.dominio import campos
from app.dominio.ficha import exigir_ficha_inteira
from app.infra.repositorios.cadastro import TICKETS, _ficha_coleta

#: Universo 1.000 e atuais 400 — a inflação do defeito era 2,5x.
LINHA = {
    "sub_bacia": "b1",
    **{coluna: None for coluna in campos.COLETA},
    **{coluna: None for coluna in campos.SO_DA_SUBBACIA},
    "universo_ligacoes": 1000,
    "ligacoes_atuais": 400,
    "receita_arrecadada_media_mensal": 180000.0,
    "receita_faturada_media_mensal": 200000.0,
    "atualizado_em": None,
    "atualizado_por": None,
}


def _db(**sobrescreve):
    return _ficha_coleta({**LINHA, **sobrescreve}, "sub_bacia")["db"]


def test_o_denominador_e_o_universo_e_nao_as_atuais():
    db = _db()
    assert db["ticket"] == "180"        # 180.000 / 1.000 — e nao 450 (/400)
    assert db["ticketFat"] == "200"     # 200.000 / 1.000 — e nao 500 (/400)


def test_as_duas_bases_de_receita_viram_dois_campos():
    assert set(TICKETS) == {"ticket", "ticketFat"}
    assert set(_db()) >= set(TICKETS)


def test_sem_universo_o_campo_sai_vazio_e_nao_zero():
    """Vazio diz "não há conta"; zero afirmaria ticket nulo onde a conta não existe.
    O caso real é a `_com_cts` vazia: o coletor levou a sub-bacia inteira."""
    db = _db(universo_ligacoes=None)
    assert db["ticket"] == "" and db["ticketFat"] == ""
    assert _db(universo_ligacoes=0)["ticket"] == ""


def test_sem_receita_o_campo_sai_vazio():
    db = _db(receita_arrecadada_media_mensal=None)
    assert db["ticket"] == ""
    assert db["ticketFat"] == "200", "a outra base não some junto"


def test_nenhum_dos_dois_entra_no_contrato_do_put():
    """São conta do servidor: exigi-los no corpo obrigaria o cliente a devolver o
    que o servidor mesmo fez. A ficha lida tem de poder ser reenviada como veio."""
    assert not set(TICKETS) & set(campos.CAMPOS_DB)
    corpo = {"db": {c: "1" for c in campos.CAMPOS_DB},
             "params": {c: "1" for c in campos.CAMPOS_PARAMS}}
    exigir_ficha_inteira(corpo)                          # sem os dois: passa
    corpo["db"].update({chave: "1" for chave in TICKETS})
    exigir_ficha_inteira(corpo)                          # com os dois: passa igual


def test_a_cts_tem_os_dois_tambem():
    """A ficha é a mesma nas duas tabelas — `COLETA` é compartilhada."""
    linha = {k: v for k, v in LINHA.items() if k not in set(campos.SO_DA_SUBBACIA)}
    db = _ficha_coleta({**linha, "cts": "c1"}, "cts")["db"]
    assert db["ticket"] == "180" and db["ticketFat"] == "200"
