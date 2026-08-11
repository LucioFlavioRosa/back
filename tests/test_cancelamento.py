"""Quando o cancelamento e aceito, e o que ele responde quando nao e.

Regras de dominio puras — nenhum teste aqui abre conexao. O que o banco garante
(o UPDATE condicional que perde a corrida contra a publicacao) esta em
`app/infra/repositorios/controle.py` e depende de Postgres; o que se pode fixar
sem ele e a decisao: quem pode ser cancelado, e o que se diz a quem nao pode.
"""

import pytest

from app.dominio import status as st


@pytest.mark.parametrize("estado", [st.Status.PENDENTE, st.Status.RODANDO])
def test_em_voo_pode_ser_cancelada(estado):
    assert st.pode_cancelar(estado)
    assert st.motivo_para_recusar_cancelamento(estado) is None


@pytest.mark.parametrize(
    "estado",
    [st.Status.SUCESSO, st.Status.ERRO, st.Status.FALHOU_QUALIDADE, st.Status.CANCELADA],
)
def test_rodada_que_ja_parou_recusa_com_motivo_legivel(estado):
    # O 409 e lido pelo usuario. Ele diz o que JA aconteceu, porque a acao que a
    # pessoa queria — parar a rodada — ja esta feita de um jeito ou de outro.
    assert not st.pode_cancelar(estado)
    assert st.motivo_para_recusar_cancelamento(estado)


def test_cancelada_diz_que_ja_foi_cancelada():
    # Duplo clique cai aqui, e "esta rodada ja terminou (status CANCELADA)" leria
    # como se outra coisa tivesse acontecido.
    assert st.motivo_para_recusar_cancelamento(st.Status.CANCELADA) == (
        "Esta rodada já foi cancelada."
    )


def test_sem_estado_registrado_nao_e_cancelavel():
    # Janela entre o INSERT da `run_request` e o job pegar a mensagem: nao ha
    # linha em `run_status`, e o UPDATE condicional nao acharia o que marcar.
    assert not st.pode_cancelar(None)
    assert "não há execução para cancelar" in st.motivo_para_recusar_cancelamento(None)


def test_cancelada_e_terminal_e_nao_e_em_voo():
    # As duas listas mandam em coisas diferentes: `TERMINAIS` faz o front parar o
    # polling, `EM_VOO` faz o `/reexecutar` recusar. Uma rodada cancelada parou de
    # vez, e pode ser reexecutada — o `run_id` nao publicou nada.
    assert st.Status.CANCELADA in st.TERMINAIS
    assert st.Status.CANCELADA not in st.EM_VOO
    assert st.pode_reexecutar(st.Status.CANCELADA)
