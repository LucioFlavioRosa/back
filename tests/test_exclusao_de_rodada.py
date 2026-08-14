"""Excluir uma rodada que NUNCA PUBLICOU tem de responder sucesso.

O defeito que estes testes guardam foi relatado por quem usa: "simulações que
apresentaram erro dão mensagem de que não é possível excluir; as que deram certo
excluem normalmente".

A causa era o valor de retorno, e não a exclusão. `excluir()` apaga cinco tabelas
numa transação, mas devolvia o resultado do PRIMEIRO delete — o de
`public.otim_meta`, que só tem linha para rodada PUBLICADA. Para `ERRO`,
`PENDENTE` e `CANCELADA` ele dava `DELETE 0`, a função devolvia `False`, e a API
levantava 404 "Rodada não encontrada" DEPOIS de a transação ter commitado.

Ou seja: a rodada era apagada e a tela dizia que não deu. Pior que um erro
honesto, porque ensina a desconfiar de uma operação que funcionou — e some da
lista só no refresh seguinte, o que parece dado fantasma.

Nenhum teste aqui abre conexão: a conexão é falsa e devolve as etiquetas que o
asyncpg devolveria. O que se fixa é a DECISÃO — quais tabelas contam para dizer
"esta rodada existia".

Os testes são SÍNCRONOS e usam `asyncio.run`, e não `pytest-asyncio`: o plugin está
na máquina mas não em `requirements.txt` nem configurado em `pytest.ini`. Depender
dele faria a suíte passar aqui e não passar no CI, que é a pior das falhas.
"""

import asyncio
import contextlib

import pytest

from app.infra.repositorios import resultado


class ConexaoFalsa:
    """Devolve a etiqueta de cada DELETE conforme a tabela, e guarda a ordem."""

    def __init__(self, linhas_por_tabela: dict[str, int]):
        self._linhas = linhas_por_tabela
        self.executados: list[str] = []

    async def execute(self, sql: str, *args) -> str:
        tabela = sql.split("FROM ")[1].split(" ")[0].split(".")[-1]
        self.executados.append(tabela)
        return f"DELETE {self._linhas.get(tabela, 0)}"


@pytest.fixture
def conexao(monkeypatch):
    """Troca `db.transacao()` por uma conexão de mentira, sem tocar em rede."""

    def fabricar(linhas: dict[str, int]) -> ConexaoFalsa:
        con = ConexaoFalsa(linhas)

        @contextlib.asynccontextmanager
        async def falsa():
            yield con

        monkeypatch.setattr(resultado.db, "transacao", falsa)
        return con

    return fabricar


def test_rodada_publicada_continua_respondendo_sucesso(conexao):
    # O caminho que sempre funcionou. Ele entra aqui para a correção não ser
    # medida só pelo caso que estava quebrado.
    conexao({"otim_meta": 1, "run_status": 1, "run_request": 1})
    assert asyncio.run(resultado.excluir("run_20260814_013909_17b0e3")) is True


@pytest.mark.parametrize("estado", ["ERRO", "PENDENTE", "CANCELADA"])
def test_rodada_que_nunca_publicou_tambem_e_excluida(conexao, estado):
    # O defeito relatado. Sem linha em `otim_meta` — que é o que caracteriza estes
    # três estados —, a resposta era 404 sobre uma exclusão que aconteceu.
    conexao({"otim_meta": 0, "run_status": 1, "run_request": 1})
    assert asyncio.run(resultado.excluir(f"run_de_teste_{estado}")) is True


def test_run_id_que_nao_existe_em_lugar_nenhum_continua_dando_404(conexao):
    # A correção não pode transformar todo DELETE em sucesso: `False` aqui é o que
    # faz a API responder 404, e um 204 sobre id inexistente esconderia erro de
    # digitação e link velho.
    conexao({})
    assert asyncio.run(resultado.excluir("run_que_nunca_existiu")) is False


def test_marca_orfa_nao_faz_a_rodada_existir(conexao):
    # `run_favorita` e `run_comentario` não têm FK (migrações 009 e 010), então uma
    # linha delas pode sobreviver a uma rodada apagada por outro caminho. Contá-la
    # como existência responderia 204 sobre um `run_id` que não existe.
    con = conexao({"run_favorita": 1, "run_comentario": 1})
    assert asyncio.run(resultado.excluir("run_so_com_satelites")) is False
    assert "run_favorita" in con.executados, "a limpeza dos satélites tem de acontecer"


def test_apaga_as_seis_tabelas_na_mesma_transacao(conexao):
    # A lista inteira, e não só as três que contam para o retorno: `run_diagnostico`
    # e os dois satélites não têm quem os apague além daqui.
    con = conexao({"otim_meta": 1})
    asyncio.run(resultado.excluir("run_20260814_013909_17b0e3"))
    assert con.executados == [
        "otim_meta",
        "run_diagnostico",
        "run_status",
        "run_request",
        "run_favorita",
        "run_comentario",
    ]
