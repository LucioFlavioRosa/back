"""`response_model` nao pode comer campo que a rota monta.

O modelo FILTRA o que sai: campo nao declarado some da resposta, sem erro e sem
aviso. Quando os modelos entraram, em 29/08/2026, a conferencia foi comparar o
payload das 28 rotas GET antes e depois — e ela deixou passar exatamente um
caso, `fila`, porque o bloco so existe em rodada PENDENTE ou RODANDO e o banco
nao tinha nenhuma no ar naquela hora.

E a licao deste arquivo: conferencia que depende do dado que por acaso existe nao
cobre o ramo que por acaso nao existe. Aqui o ramo e construido.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import simulacao
from main import app


@pytest.fixture
def cliente():
    return TestClient(app)


def _rodada(monkeypatch, status: str, progresso: int = 0):
    async def status_(_run_id):
        return {
            "status": status,
            "progresso": progresso,
            "erro": None,
            "solicitado_em": datetime(2026, 8, 29, tzinfo=timezone.utc),
        }

    async def executores():
        return {"vivos": 1, "capacidade": 4, "ocupadas": 4}

    async def posicao(_run_id):
        return 2

    monkeypatch.setattr(simulacao.controle, "status", status_)
    monkeypatch.setattr(simulacao.controle, "executores", executores)
    monkeypatch.setattr(simulacao.controle, "posicao_na_fila", posicao)
    # A rota e protegida pela `guarda_de_rota`, que le o recorte do usuario; sem
    # banco ela nao tem como responder, entao sai do caminho.
    app.dependency_overrides[simulacao.guarda_de_rota] = lambda: None


@pytest.fixture(autouse=True)
def _limpar_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize("estado", ["PENDENTE", "RODANDO"])
def test_rodada_em_voo_leva_o_bloco_fila(cliente, monkeypatch, estado):
    """`fila` responde "por que esta rodada esta onde esta" — e a tela depende dele.

    Sem ele, "esperando um executor" cobre dois mundos opostos: fila cheia com
    executor trabalhando, e NENHUM executor no ar.
    """
    _rodada(monkeypatch, estado)
    corpo = cliente.get("/api/runs/run_20260829_000000_abcdef/status").json()

    assert "fila" in corpo, "o `response_model` comeu o bloco `fila`"
    assert corpo["fila"]["capacidade"] == 4
    assert corpo["fila"]["motivo"], "a explicacao em portugues nao pode vir vazia"
    assert set(corpo["fila"]) == {
        "vivos", "capacidade", "ocupadas", "posicao", "motivo", "atencao",
    }


@pytest.mark.parametrize("estado", ["SUCESSO", "ERRO", "CANCELADA"])
def test_rodada_terminada_nao_leva_fila(cliente, monkeypatch, estado):
    """AUSENTE, e nao `null`: rodada terminada nao esta em fila nenhuma.

    `null` diria "tem fila, e ela esta vazia". E o `response_model_exclude_unset`
    da rota que preserva a diferenca.
    """
    _rodada(monkeypatch, estado, progresso=100)
    corpo = cliente.get("/api/runs/run_20260829_000000_abcdef/status").json()

    assert "fila" not in corpo
    assert corpo["progresso"] == 100
