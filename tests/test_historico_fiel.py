"""O que este teste protege: o histórico mostra o que a PESSOA pediu.

Dois defeitos relatados pelo dono do produto, na mesma tela e com a mesma raiz —
o histórico lia de `public.otim_meta`, que é escrita pelo EXECUTOR, quando o
pedido está em `controle.run_request`, escrito por este serviço:

  NOME     uma rodada chamada "teste" aparecia como "uA2 — pela tela". Um
           executor com o bug do fallback (`dev/worker.py`, corrigido em
           bbc8173) publica o rótulo genérico por cima do nome digitado, e a
           pessoa procura na lista um nome que não está lá.

  HORÁRIO  `otim_meta.data_hora` vem do pacote do otimizador, que grava o relógio
           LOCAL numa coluna `timestamptz`; medido em BRT, fica 3h deslocado. E,
           como `em_voo` usava `solicitado_em` e `historico` usava `data_hora`, a
           MESMA rodada mudava de horário ao terminar e pulava de posição na
           lista — de novo parecendo que sumiu.

Os dois casos são propriedades do dado que existe, e por isso o teste roda contra
o banco carregado: fixture não reproduziria um executor com bug.
"""

import asyncio
import os

import pytest

from app.infra import db


def _banco_disponivel() -> bool:
    return bool(os.environ.get("POSTGRES_URL", "").endswith("/otimizador"))


pytestmark = pytest.mark.skipif(
    not _banco_disponivel(), reason="sem banco real (POSTGRES_URL de teste)"
)


def _rodar(corrotina):
    async def envolver():
        await db.abrir_pool()
        try:
            return await corrotina()
        finally:
            await db.fechar_pool()

    return asyncio.run(envolver())


def _historico():
    from app.infra.repositorios import resultado

    return _rodar(resultado.historico)


def _pedidos():
    async def ler():
        linhas = await db.buscar(
            "SELECT run_id, rotulo, solicitado_em FROM controle.run_request"
        )
        return {l["run_id"]: l for l in linhas}

    return _rodar(ler)


def test_o_nome_da_rodada_e_o_que_a_pessoa_digitou():
    """Onde há pedido com nome, o histórico mostra ESSE nome.

    Sem isto, basta um executor desatualizado para a lista inteira virar
    "uA1 — pela tela" e o usuário perder a rodada que acabou de disparar.
    """
    pedidos = _pedidos()
    divergentes = [
        (r["runId"], r.get("nome"), pedidos[r["runId"]]["rotulo"])
        for r in _historico()
        if r["runId"] in pedidos and pedidos[r["runId"]]["rotulo"]
        and r.get("nome") != pedidos[r["runId"]]["rotulo"]
    ]
    assert not divergentes, (
        "o histórico está mostrando o rótulo do EXECUTOR no lugar do nome pedido: "
        f"{divergentes[:5]}"
    )


def test_o_horario_e_o_do_pedido():
    """Onde há pedido, o horário é `solicitado_em` — o relógio deste serviço.

    `otim_meta.data_hora` é do executor e pode estar em qualquer fuso; a lista
    não pode depender disso para ordenar.
    """
    pedidos = _pedidos()
    divergentes = [
        (r["runId"], r.get("dataHora"), pedidos[r["runId"]]["solicitado_em"].isoformat())
        for r in _historico()
        if r["runId"] in pedidos
        and r.get("dataHora") != pedidos[r["runId"]]["solicitado_em"].isoformat()
    ]
    assert not divergentes, f"horário fora do pedido: {divergentes[:5]}"


def test_rodada_publicada_por_fora_da_fila_continua_na_lista():
    """Sem `run_request`, o COALESCE cai no que o executor gravou.

    É o caso das rodadas publicadas direto pelo pacote de produção. Elas não têm
    pedido, e some-las da lista para "proteger" a fidelidade seria esconder
    resultado real.
    """
    pedidos = _pedidos()
    sem_pedido = [r for r in _historico() if r["runId"] not in pedidos]
    for r in sem_pedido:
        assert r.get("dataHora"), f"{r['runId']} ficou sem horário nenhum"
        assert r.get("nome"), f"{r['runId']} ficou sem nome nenhum"


def test_a_lista_esta_ordenada_do_mais_recente_para_o_mais_antigo():
    """A ordenação usa o MESMO campo que a tela mostra.

    Ordenar por um campo e exibir outro é o que fazia a rodada saltar de posição
    ao terminar.
    """
    datas = [r["dataHora"] for r in _historico() if r.get("dataHora")]
    assert datas == sorted(datas, reverse=True), "o histórico não está em ordem decrescente"
