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
"""

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
