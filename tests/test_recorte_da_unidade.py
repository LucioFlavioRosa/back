"""O RECORTE DE CIDADES DA UNIDADE SE ESCREVE UMA VEZ SÓ.

Não há coluna `unidade_id` nas tabelas de baixo: quem pertence a quem sai do
encadeamento `unidade → empresa → cidade_empresa → cidade`. Quase toda consulta
do cadastro precisa desse caminho, e por isso ele reaparecia escrito à mão —
foram cinco cópias, em `pendencias` e em `controle`, todas equivalentes e
nenhuma igual à outra na forma.

O CUSTO DE UMA CÓPIA NÃO É ESTÉTICO. O recorte, errado, faz a tela mostrar dado
de outra unidade sem nenhum sinal — não há erro, não há linha vermelha, só um
número que pertence a outro lugar. E quando a hierarquia muda (a diretoria entrou
na migração 017), corrigir uma definição é diferente de caçar cinco.

Este arquivo é o guarda-corpo. Ele não confere que as consultas estão certas —
isso é o que os smokes de `dev/` fazem, contra banco. Ele confere que a definição
continua UMA.
"""

import re
from pathlib import Path

import pytest

from app.infra.repositorios.recortes import CIDADES_DA_UNIDADE

REPOSITORIOS = Path(__file__).resolve().parents[1] / "app"

#: A ASSINATURA DE UM RECORTE ESCRITO À MÃO: sair de `empresa` filtrando pela
#: unidade. É o começo do caminho, e é o que toda cópia tem em comum, por mais
#: que difiram no resto — CTE ou JOIN, uma coluna ou três.
#:
#: Procurar por `cidade_empresa` sozinho não serviria: ele aparece legitimamente
#: em consultas que já receberam o recorte e só precisam da empresa de uma cidade.
#:
#: `[^"]` NO MEIO, E NÃO `.`: o SQL vive dentro de strings Python, e nelas não há
#: ponto-e-vírgula separando uma consulta da outra. Sem essa restrição o casamento
#: atravessa a aspa tripla e junta o `JOIN empresa` de uma consulta com o
#: `unidade_id = $1` da consulta SEGUINTE — foi assim que a primeira versão deste
#: teste acusou duas consultas que fazem a pergunta INVERSA ("de que unidade é
#: este componente?"), onde `$1` é o componente e não a unidade.
A_MAO = re.compile(
    r"\{i\}\.empresa\s+\w+\s+(USING\s*\(emp_codigo\)|ON\s+[\w.]+\s*=\s*[\w.]+)"
    r"[^\";]{0,300}?unidade_id\s*=\s*\$1",
    re.IGNORECASE | re.DOTALL,
)


def _fontes():
    for caminho in sorted(REPOSITORIOS.rglob("*.py")):
        if caminho.name == "recortes.py":
            continue
        yield caminho


@pytest.mark.parametrize("caminho", list(_fontes()), ids=lambda c: c.name)
def test_ninguem_reescreve_o_recorte_da_unidade(caminho):
    """Uma sexta cópia falha aqui, e não dois meses depois numa tela errada."""
    texto = caminho.read_text(encoding="utf-8")
    # `{_i()}` é a mesma coisa que `{i}` — a diferença é só de quem formata.
    achados = A_MAO.findall(texto.replace("{_i()}", "{i}"))
    assert not achados, (
        f"{caminho.name} monta o recorte de cidades da unidade à mão. Use "
        "`repositorios.recortes.CIDADES_DA_UNIDADE` — o recorte errado mostra "
        "dado de outra unidade sem nenhum sinal."
    )


def test_o_recorte_traz_o_que_os_chamadores_precisam():
    """As três colunas, e a unidade como `$1`.

    `emp_codigo` está lá porque metade dos chamadores precisa dele em seguida — a
    macrorregião de CTS é agrupada por `(sistema_cts, emp_codigo)`. Tirá-lo
    obrigaria um segundo `JOIN` sobre a mesma tabela, que é como as cópias
    começam.
    """
    for coluna in ("c.cidade_id", "c.cidade_name", "ce.emp_codigo"):
        assert coluna in CIDADES_DA_UNIDADE
    assert "$1" in CIDADES_DA_UNIDADE
    assert "{i}" in CIDADES_DA_UNIDADE, "o schema tem de continuar parametrizado"


def test_o_recorte_sai_da_cidade_e_nao_do_vinculo():
    """`FROM cidade` — e não `FROM cidade_empresa`, como as cópias faziam.

    Hoje dá no mesmo: `cidade_empresa.cidade_id` é FK para `cidade`, então o JOIN
    não descarta linha nenhuma (conferido no dump de 03/09/2026: 141 vínculos, 0
    sem município). A forma importa mesmo assim — quem lê precisa ver que a lista
    é de MUNICÍPIOS, e não de vínculos, e `cidade_name` só existe deste lado.
    """
    inicio = CIDADES_DA_UNIDADE.index("FROM")
    assert "{i}.cidade c" in CIDADES_DA_UNIDADE[inicio : inicio + 40]
