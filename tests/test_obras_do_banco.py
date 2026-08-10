"""A obra vem do BANCO, e obra que falta é recusa — nunca preenchimento.

## O que este arquivo substituiu

Aqui havia `test_base_obras.py`, que comparava duas listas literais de obras — uma
em Python, outra em TypeScript — e falhava quando divergiam. Era um bom teste para
um desenho errado: as duas bases existiam, e a existência delas é que violava R1 e
R2.

O modo de falha que elas produziam, medido antes de sair: um `PUT` numa ficha sem
o componente gravado escrevia `Linha de recalque (LR) | qtd 0 | preco 900 | dur 15
| wacc 0,067`. Nenhum daqueles números veio do banco nem de alguém digitando. Iam
para a simulação com cara de cadastro.

As duas bases saíram (`_BASE_SUBBACIA`/`_BASE_CTS` aqui, `BASE_OBRAS`/
`BASE_OBRAS_CTS` no front). O que este arquivo prova agora é o desenho que ficou:

  1. a materialização tem uma fonte só — a linha gravada;
  2. componente ausente **recusa** a gravação, com a mesma régua do `/prontidao`;
  3. o literal não voltou, dos dois lados;
  4. o banco de verdade tem a cardinalidade que a régua afirma.

Os três primeiros são Python puro. O quarto abre conexão e é PULADO quando não há
banco — CI sem Postgres não pode ficar vermelho por ausência de infraestrutura,
e os smokes (`dev/`) são o lugar onde o banco é obrigatório.
"""

import asyncio
import os
import re
from pathlib import Path

import pytest

from app.infra.repositorios.cadastro_escrita import ValorInvalido, _obras_da_ficha
from app.infra.repositorios.pendencias import OBRAS_CTS, OBRAS_SUBBACIA

#: Uma ficha de sub-bacia como o banco a devolve: cinco componentes, com o nome
#: que o motor casa (`otimizador_capex_v62.py:1136`) e valores que são do BANCO —
#: não de um template. Os `preco` diferentes entre si existem para que um merge
#: errado apareça como valor trocado, e não como coincidência.
ATUAL = {
    "0": {"nome": "Ligacao de esgoto", "un": "ligacao", "qtd": 244.0, "preco": 2497.7},
    "1": {"nome": "Rede coletora", "un": "m", "qtd": 2472.6, "preco": 449.99},
    "2": {"nome": "Coletor tronco", "un": "m", "qtd": 1520.47, "preco": 1314.5},
    "3": {"nome": "Estacao elevatoria (EEE)", "un": "un", "qtd": 1.0, "preco": 534461.86},
    "4": {"nome": "Linha de recalque (LR)", "un": "m", "qtd": 1103.15, "preco": 989.3},
}
TODOS = {i: {} for i in ATUAL}


def _ficha(override, atual=None, esperadas=OBRAS_SUBBACIA):
    return _obras_da_ficha(override, atual or ATUAL, esperadas=esperadas, rotulo="sub-bacia")


# ----------------------------------------------- 1. a fonte é a linha gravada
def test_a_obra_sai_do_banco_e_o_corpo_so_sobrepoe():
    obras = _ficha({**TODOS, "0": {"qtd": "999"}})
    assert obras[0]["qtd"] == "999"  # o que o usuário digitou
    assert obras[0]["preco"] == 2497.7  # do BANCO, e não de um literal
    assert obras[0]["nome"] == "Ligacao de esgoto"
    assert [o["nome"] for o in obras] == [ATUAL[str(i)]["nome"] for i in range(5)]


def test_o_nome_gravado_e_o_do_banco_e_nao_o_da_outra_tabela():
    """A CTS chama `Tronco` o que a sub-bacia chama `Coletor tronco`.

    A base literal usava o vocabulário da sub-bacia nas duas tabelas, então
    regravar uma CTS trocava os nomes — e o motor deixava de reconhecer o
    componente, sem erro nenhum no caminho. Vindo da linha gravada, cada tabela
    conserva o vocabulário dela.
    """
    cts = {
        "0": {"nome": "Coletor de tempo seco"},
        "1": {"nome": "Tronco"},
        "2": {"nome": "EEE"},
        "3": {"nome": "Linha de recalque"},
    }
    obras = _obras_da_ficha(
        {i: {} for i in cts}, cts, esperadas=OBRAS_CTS, rotulo="cts"
    )
    assert [o["nome"] for o in obras] == ["Coletor de tempo seco", "Tronco", "EEE", "Linha de recalque"]


# ------------------------------------------------------ 2. o que é RECUSA
def test_componente_faltando_no_banco_recusa_a_gravacao():
    """O caso que a base literal escondia com números plausíveis.

    Recusar é a única resposta honesta: os campos do componente que falta não
    existem em lugar nenhum, e completá-los aqui seria inventá-los.
    """
    sem_a_lr = {i: v for i, v in ATUAL.items() if i != "4"}
    with pytest.raises(ValorInvalido) as e:
        _ficha({i: {} for i in sem_a_lr}, atual=sem_a_lr)
    assert "4 componentes" in str(e.value) and "5" in str(e.value)
    # A mensagem manda para onde a correção é possível, e não para o suporte.
    assert "/prontidao" in str(e.value)


def test_componente_omitido_no_corpo_recusa_e_diz_qual():
    """Sem isto ele seria APAGADO: a gravação substitui as obras em bloco."""
    with pytest.raises(ValorInvalido) as e:
        _ficha({"0": {"qtd": "1"}, "1": {}})
    assert "Coletor tronco" in str(e.value)


def test_indice_que_a_ficha_nao_tem_recusa():
    """Gravar um índice inexistente criaria obra a partir do payload.

    É a base literal por outro caminho: a linha nasceria do que o cliente mandou,
    e não do que o banco tem.
    """
    with pytest.raises(ValorInvalido) as e:
        _ficha({**TODOS, "9": {"qtd": "1"}})
    assert "9" in str(e.value)


def test_ficha_vazia_no_banco_recusa_em_vez_de_criar_as_cinco():
    with pytest.raises(ValorInvalido):
        _ficha({}, atual={})


def test_lista_antiga_continua_passando():
    """Forma antiga (lista pronta), que os smokes locais ainda usam.

    Ela pula a materialização inteira: quem manda a lista já a montou. Continua
    aceita porque não há base para completar nada — o risco que a base criava não
    existe neste caminho.
    """
    assert _ficha([{"qtd": "1", "nome": "Rede coletora"}]) == [{"qtd": "1", "nome": "Rede coletora"}]


# --------------------------------------------- 3. o literal não voltou
BACKEND = Path(__file__).resolve().parents[1]
FRONT = BACKEND.parent / "otimizador-cadastro-web"


def test_o_backend_nao_tem_mais_base_literal_de_obra():
    """Guarda-corpo contra a reintrodução.

    A base voltar é fácil e silencioso — basta alguém "consertar" a recusa acima
    completando o que falta. Este teste torna isso uma falha vermelha.
    """
    fonte = (BACKEND / "app/infra/repositorios/cadastro_escrita.py").read_text(encoding="utf-8")
    assert "_BASE_SUBBACIA" not in fonte and "_BASE_CTS" not in fonte
    # A FORMA de uma obra-base, e não um valor específico: um dicionário que traz
    # nome e preço juntos é uma linha de obra nascendo do código. Procurar pelos
    # números antigos pegaria só a base que saiu; a forma pega a próxima.
    molde = [
        l for l in fonte.splitlines() if '"nome":' in l and '"preco":' in l
    ]
    assert not molde, f"obra literal de volta em cadastro_escrita.py: {molde[:2]}"


@pytest.mark.skipif(not FRONT.exists(), reason="repositório do front não está ao lado")
def test_o_front_nao_tem_mais_base_literal_de_obra():
    """O outro lado da mesma regra.

    Ler o repositório vizinho tem precedente aqui (`test_contrato.py` lê o
    `DEPLOY.md`). Se ele não estiver ao lado, o teste é pulado — CI de um repo só
    não pode ficar vermelho por ausência do outro.
    """
    for arquivo, constante in (
        ("src/cadastro/domain/subbacia.ts", "BASE_OBRAS"),
        ("src/cadastro/domain/cts.ts", "BASE_OBRAS_CTS"),
    ):
        fonte = (FRONT / arquivo).read_text(encoding="utf-8")
        assert not re.search(rf"export const {constante}\b", fonte), (
            f"{constante} voltou em {arquivo}. A tabela de obras tem de sair do "
            "que o GET mandou — o backend passou a enviar `nome` e `un`."
        )


# --------------------------------------- 4. o banco confirma a cardinalidade
#: Os nomes de cada tabela, como estão no banco carregado da planilha. Não são
#: rótulo de tela: são a IDENTIDADE que o motor casa com o componente.
NOMES = {
    "componentes_subbacias_capex": {
        "Ligacao de esgoto",
        "Rede coletora",
        "Coletor tronco",
        "Estacao elevatoria (EEE)",
        "Linha de recalque (LR)",
    },
    "componentes_cts_capex": {
        "Coletor de tempo seco",
        "Tronco",
        "EEE",
        "Linha de recalque",
    },
}


def _banco_disponivel() -> bool:
    return bool(os.environ.get("POSTGRES_URL", "").endswith("/otimizador"))


@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real (POSTGRES_URL de teste)")
@pytest.mark.parametrize(
    "tabela,chave,esperadas",
    [
        ("componentes_subbacias_capex", "sub_bacia", OBRAS_SUBBACIA),
        ("componentes_cts_capex", "cts", OBRAS_CTS),
    ],
)
def test_toda_ficha_do_banco_tem_a_cardinalidade_da_regua(tabela, chave, esperadas):
    """A régua da recusa tem de descrever o banco que existe.

    Se ela afirmar 5 e houver fichas com 4, o `PUT` passa a recusar cadastro que
    hoje é salvo — e o erro apareceria em produção, para o usuário, e não aqui.
    """

    async def medir():
        from app.infra import db

        await db.abrir_pool()
        try:
            return await db.buscar(
                f"""SELECT count(*) AS fichas,
                           min(n) AS menor, max(n) AS maior,
                           count(*) FILTER (WHERE n <> $1) AS fora
                      FROM (SELECT {chave}, count(*) AS n
                              FROM input.{tabela} GROUP BY {chave}) t""",
                esperadas,
            )
        finally:
            await db.fechar_pool()

    linha = asyncio.run(medir())[0]
    assert linha["fichas"] > 0, f"{tabela} está vazia — o banco não foi carregado"
    assert linha["fora"] == 0, (
        f"{tabela}: {linha['fora']} ficha(s) com número de componentes diferente de "
        f"{esperadas} (menor={linha['menor']}, maior={linha['maior']}). Ou o cadastro "
        "perdeu componente, ou a régua está errada — as duas exigem decisão humana."
    )


@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real (POSTGRES_URL de teste)")
@pytest.mark.parametrize("tabela", list(NOMES))
def test_os_nomes_dos_componentes_sao_os_que_o_de_para_conhece(tabela):
    """Nome fora do de-para vira componente que o `GET` ignora e o motor não acha."""

    async def medir():
        from app.infra import db

        await db.abrir_pool()
        try:
            return await db.buscar(f"SELECT DISTINCT componente FROM input.{tabela}")
        finally:
            await db.fechar_pool()

    achados = {l["componente"] for l in asyncio.run(medir())}
    assert achados == NOMES[tabela], (
        f"{tabela}: nomes inesperados {sorted(achados - NOMES[tabela])}, "
        f"faltando {sorted(NOMES[tabela] - achados)}"
    )
