"""A deduplicação de rodada é por PEDIDO **e por PESSOA**.

Este arquivo existe porque a regra já esteve errada de um jeito que nenhum teste
pegava, e o erro não era de código: era uma premissa que envelheceu.

O `digest` excluía o `USUARIO`, com um argumento bom para a época — duas pessoas
pedindo a mesma simulação pedem a mesma coisa, e rodar duas vezes gasta cluster
para produzir dois resultados idênticos. Isso valia enquanto as rodadas eram
COMPARTILHADAS.

Quando a posse passou a ser por pessoa, a mesma linha de código virou uma
promessa que o serviço nega em seguida: o segundo pedinte recebia `200` com o
`runId` do primeiro, e levava `404` ao abrir. Dizer "pronto, é essa" e depois
negar que existe é pior que gastar cluster duas vezes.

Os dois lados são testados juntos de propósito. Deduplicar demais quebra a posse;
deduplicar de menos gasta cluster à toa — e é a combinação que define a regra, não
cada metade sozinha.

A dedupe passou a alcançar a rodada **concluída** (R5), e o `digest` não mudou por
causa disso: quem decide "é o mesmo pedido" continua sendo ele, e quem decide "essa
rodada ainda serve" é o SQL de `rodada_identica`. A parte SQL é verificada contra o
banco real pelos smokes — aqui fica a régua que não precisa de banco, mais a
conferência de que a consulta afirma as três condições que a tornam correta.
"""

import re
from pathlib import Path

from app.infra.repositorios.controle import digest

PEDIDO = {
    "UNIDADE": "uA1",
    "ORCAMENTO": {"2026": 60_000_000.0},
    "BASE_RECEITA": "arrecadada",
    "USAR_CTS": True,
}


def test_mesma_pessoa_mesmo_pedido_deduplica():
    """Duplo clique, retry do navegador, reenvio do SDK: uma rodada só."""
    a = digest({**PEDIDO, "USUARIO": "ana@aegea"})
    b = digest({**PEDIDO, "USUARIO": "ana@aegea"})
    assert a == b


def test_pessoas_diferentes_nao_deduplicam():
    """O caso que a regra antiga errava — e que o guarda de posse denunciava
    depois, com um 404 numa rodada que o próprio serviço acabara de indicar."""
    ana = digest({**PEDIDO, "USUARIO": "ana@aegea"})
    carlos = digest({**PEDIDO, "USUARIO": "carlos@aegea"})
    assert ana != carlos


def test_parametros_diferentes_nao_deduplicam():
    """Comparar cenários é o uso normal do produto: mudar orçamento, desligar CTS
    ou trocar a base de receita tem de gerar rodada nova, para a mesma pessoa."""
    base = {**PEDIDO, "USUARIO": "ana@aegea"}
    assert digest(base) != digest({**base, "ORCAMENTO": {"2026": 80_000_000.0}})
    assert digest(base) != digest({**base, "USAR_CTS": False})
    assert digest(base) != digest({**base, "BASE_RECEITA": "faturada"})


def test_ordem_das_chaves_nao_muda_o_digest():
    """Dois clientes montando o mesmo corpo em ordens diferentes pedem a MESMA
    coisa — sem `sort_keys` eles gerariam duas execuções idênticas."""
    a = digest({"USUARIO": "ana@aegea", **PEDIDO})
    b = digest({**{k: PEDIDO[k] for k in reversed(list(PEDIDO))}, "USUARIO": "ana@aegea"})
    assert a == b


# --------------------------------------------------------------------------
# A dedupe de rodada CONCLUÍDA. O que dá para provar sem banco é que a consulta
# continua exigindo as três condições — cada uma existe por um motivo diferente,
# e perder qualquer uma produz um defeito diferente.

FONTE = (Path(__file__).resolve().parents[1] / "app/infra/repositorios/controle.py").read_text(
    encoding="utf-8"
)


def test_a_concluida_so_conta_se_estiver_publicada():
    """`SUCESSO` sem linha em `otim_meta` diz que deu certo e não tem o que abrir.

    Mandar alguém para uma rodada assim é prometer uma tela vazia.
    """
    assert "otim_meta m WHERE m.run_id = r.run_id" in FONTE


def test_a_concluida_so_conta_se_o_cadastro_nao_mudou_depois():
    """A condição que impede a dedupe de violar a R1.

    Os mesmos parâmetros de TELA não são a mesma simulação se o CADASTRO mudou no
    meio: a rodada de ontem leu preços e obras que não são os de hoje. A conta usa
    `atualizado_em`, que só existe desde a auditoria por ficha.
    """
    assert "solicitado_em > COALESCE(" in FONTE
    assert "atualizado_em" in FONTE


def test_erro_continua_liberando_execucao_nova():
    """Quem repete depois de uma falha está corrigindo algo.

    Apontá-lo para o fracasso anterior impediria a correção — por isso a consulta
    lista `PENDENTE`/`RODANDO` e `SUCESSO`, e nunca `ERRO`.
    """
    consulta = re.search(r"async def rodada_identica.*?return None", FONTE, re.S)
    assert consulta, "não achei `rodada_identica`"
    assert "Status.ERRO" not in consulta.group(0)
    assert "Status.SUCESSO.value" in consulta.group(0)


def test_a_funcao_nao_se_chama_mais_em_voo():
    """O nome antigo (`rodada_em_voo`) passou a mentir quando ela alcançou a
    concluída — e comentário ou nome que mente conta como defeito nesta base."""
    assert "def rodada_em_voo" not in FONTE
