"""Quem calcula o que mudou é o SERVIDOR, e a trilha cobre a ficha inteira.

`diferencas` compara o que está gravado com o que chegou no `PUT`, campo a campo,
e é o que a trilha registra. O corpo da requisição não informa o que mudou nem
quem assina — auditoria montada pelo cliente quebra em silêncio quando o cliente
tem bug, e cobriria só os campos que ele resolvesse montar.

Estes testes são Python puro: exercitam a comparação, que é onde a decisão mora.
O caminho até o banco é coberto pelos smokes (`dev/`), contra Postgres de verdade.
"""

import tokenize
from pathlib import Path

import pytest

from app.dominio.trilha import DATABRICKS, REGIONAL, diferencas, igual, origem_do_campo


# ------------------------------------------------------- o que conta como mudança
def test_numero_compara_como_numero_e_nao_como_texto():
    """O banco devolve `float` e o corpo traz string pt-BR.

    Sem isto, `"244" != 244.0` como texto, e cada salvamento gravaria uma linha
    dizendo que 244 virou 244 — a trilha viraria ruído em uma semana.
    """
    assert igual(244.0, "244") is False  # `diferencas` compara valores já convertidos
    assert igual(244.0, 244) is True
    assert igual(2472.6, 2472.5999999999995) is True  # ida e volta pelo driver


def test_ausencia_dos_dois_lados_nao_e_mudanca():
    assert igual(None, None) is True
    assert igual(None, 0) is False  # vazio e zero são coisas diferentes


def test_salvar_sem_mudar_nada_nao_gera_trilha():
    """A dedupe some por construção: comparando com o dado gravado, reenviar a
    mesma ficha não produz diferença — e não há o que deduplicar.

    Antes havia uma consulta só para isso, porque o cliente reenviava a trilha
    inteira a cada `PUT`."""
    ficha = {"preco": 1103.91, "tarr": 12.0, "vaz": None}
    assert diferencas(ficha, dict(ficha)) == []


# ----------------------------------------------------- o que a trilha registra
def test_registra_o_campo_com_de_e_para():
    m = diferencas({"preco": 1103.91}, {"preco": 9999.0})
    assert len(m) == 1
    assert (m[0].campo, m[0].antes, m[0].depois) == ("preco", "1.103,91", "9.999")


def test_valores_em_pt_br_porque_a_trilha_e_lida_por_gente():
    """`2497.7` numa reunião obriga quem lê a traduzir de cabeça o que a tela
    sempre mostrou como `2.497,70`."""
    m = diferencas({"x": 2497.7}, {"x": 1234.5})
    assert m[0].antes == "2.497,70" or m[0].antes == "2.497,7"
    assert "," in m[0].depois


def test_ano_e_codigo_nao_levam_separador_de_milhar():
    """`2044`, e não `2.044`.

    Pego navegando a ficha de Contrato & Metas, e não pelos testes: eles
    exercitavam preço e quantidade, onde o separador ESTÁ certo. A régua é a
    mesma da leitura (`cadastro.SEM_SEPARADOR`).

    Num ano, o ponto é erro de leitura. E como o ano é a CHAVE da meta, ele
    contaminava o identificador: a trilha gravava `meta:2.044:pct`, uma chave que
    não corresponde a nada.
    """
    assert diferencas({"fim": 2041.0}, {"fim": 2055.0})[0].antes == "2041"
    assert diferencas({"ano": None}, {"ano": 2044.0})[0].depois == "2044"

    # Prefixo não muda a régua: `obra:X:anoObrig` continua sendo um `anoObrig`.
    m = diferencas({"anoObrig": 0.0}, {"anoObrig": 2030.0}, prefixo="obra:Rede coletora:")
    assert m[0].depois == "2030"

    # E quantidade continua com separador — é o outro lado da mesma régua.
    assert diferencas({"qtd": 1.0}, {"qtd": 2841.5})[0].depois == "2.841,5"


def test_criacao_e_remocao_sao_distinguiveis():
    """`None` tem significado nos dois lados, e são significados diferentes.

    Sem isso, remover uma meta e apagar o número dela ficariam indistinguíveis na
    auditoria — ver `migracoes/007_trilha_do_cadastro.sql`.
    """
    criou = diferencas({}, {"meta:2030": 85.0})
    assert (criou[0].antes, criou[0].depois) == (None, "85")

    removeu = diferencas({"meta:2028": 70.0}, {})
    assert (removeu[0].antes, removeu[0].depois) == ("70", None)


def test_o_prefixo_leva_a_identidade_do_registro():
    """`obra:Rede coletora:qtd` — o componente é quem identifica a obra.

    Por índice (`obra:2:qtd`) seria mais curto e não diria nada a quem abre a
    auditoria seis meses depois; pior, mudaria de significado se a ordem mudasse.
    """
    m = diferencas({"qtd": 1.0}, {"qtd": 2.0}, prefixo="obra:Rede coletora:")
    assert m[0].campo == "obra:Rede coletora:qtd"


# --------------------------------------------------------------------- origem
def test_origem_separa_correcao_de_preenchimento():
    """Discordar de um número do Databricks e preencher um campo próprio são
    coisas diferentes para quem audita — e por isso viram verbos diferentes na
    tela ("corrigiu" contra "alterou")."""
    assert origem_do_campo("fat") == DATABRICKS
    assert origem_do_campo("ligA") == DATABRICKS
    assert origem_do_campo("preco") == REGIONAL
    assert origem_do_campo("vaz") == REGIONAL


def test_a_origem_pode_ser_decidida_campo_a_campo():
    """A ficha de coleta mistura os dois blocos na mesma linha do banco."""
    m = diferencas(
        {"fat": 1.0, "preco": 1.0},
        {"fat": 2.0, "preco": 2.0},
        origem=origem_do_campo,
    )
    assert {a.campo: a.origem for a in m} == {"fat": DATABRICKS, "preco": REGIONAL}


def test_a_origem_pode_ser_fixa_para_o_bloco():
    """Obra é cadastro da Regional inteiro: não há número de obra vindo do
    Databricks para corrigir."""
    m = diferencas({"qtd": 1.0}, {"qtd": 2.0}, origem=REGIONAL)
    assert m[0].origem == REGIONAL


# ------------------------------------------- o corpo não decide mais nada disto
def _codigo(caminho: Path) -> str:
    """A fonte SEM comentário e SEM docstring — só o que executa.

    Os guarda-corpos abaixo procuram construções do desenho antigo, e o arquivo
    EXPLICA esse desenho em prosa (é como se registra por que ele saiu). Buscar
    no texto cru daria falso positivo contra a própria documentação — e a saída
    fácil, apagar a explicação para o teste passar, seria perder o motivo.
    """
    partes = []
    with tokenize.open(caminho) as f:
        anterior = None
        for tok in tokenize.generate_tokens(f.readline):
            if tok.type == tokenize.COMMENT:
                continue
            # String solta (não atribuída a nada) é docstring.
            if tok.type == tokenize.STRING and anterior in (
                None,
                tokenize.INDENT,
                tokenize.NEWLINE,
                tokenize.NL,
            ):
                continue
            partes.append(tok.string)
            anterior = tok.type
    return " ".join(partes)


ESCRITA = Path(__file__).resolve().parents[1] / "app/infra/repositorios/cadastro_escrita.py"


def test_o_corpo_do_put_nao_participa_da_trilha():
    """Guarda-corpo contra a volta do desenho antigo.

    `salvar_*` não pode voltar a ler `overrides` do corpo: seria reabrir a porta
    para o cliente dizer o que mudou, e para o `autor` chegar de fora.
    """
    codigo = _codigo(ESCRITA)
    assert '"overrides"' not in codigo
    assert '"autor"' not in codigo


@pytest.mark.parametrize("campo", ["valorAntigo", "valorNovo"])
def test_o_vocabulario_do_cliente_nao_sobrevive_no_backend(campo):
    """`valorAntigo`/`valorNovo` eram as chaves que o front mandava. Some com
    elas evita que alguém religue o caminho antigo por hábito."""
    assert campo not in _codigo(ESCRITA)
