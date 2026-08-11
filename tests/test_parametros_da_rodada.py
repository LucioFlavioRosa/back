"""Os parametros que a tela oferece chegam ao motor com o significado que ela promete.

O modo de falha que estes testes guardam nao e o erro: e o SILENCIO. Um parametro
que a tela coleta, o banco grava e o motor nunca recebe faz o usuario ajustar um
controle, ver o numero mudar por outro motivo, e aprender uma relacao que nao
existe. Foi o que aconteceu com `MAX_TIME_S`/`WORKERS` antes, e depois com estes
seis.
"""

import pytest

from app.dominio.parametros import ParametrosInvalidos, mes_ano, montar_params

BASE = {"orcamento": {"2027": 60_000_000}}


def montar(**extra):
    return montar_params({**BASE, **extra}, unidade_id="uA3", usuario="ana@aegea")


class TestMetasDeCobertura:
    """A escolha da tela tem DUAS opcoes, e elas nao podem virar o mesmo valor."""

    def test_cadastro_vira_none_para_o_motor_carregar_da_planilha(self):
        # `None` no motor significa "carregue as metas da planilha" — e e isso que
        # "usar as metas do cadastro" quer dizer.
        assert montar(metas_cobertura="cadastro")["METAS_COBERTURA"] is None

    def test_ignorar_vira_dict_vazio_e_nao_none(self):
        # Este e o bug que os testes existem para nao deixar voltar: `null` da tela
        # significa IGNORAR, e virava `None` — que no motor manda carregar. Quem
        # pedia para ignorar rodava COM as metas, e a tela avisava o contrario.
        assert montar(metas_cobertura=None)["METAS_COBERTURA"] == {}

    def test_as_duas_escolhas_produzem_valores_DIFERENTES(self):
        # A asserção que pega o colapso independentemente de qual valor cada uma
        # recebe: se um dia as duas voltarem a coincidir, isto quebra.
        assert montar(metas_cobertura="cadastro")["METAS_COBERTURA"] != (
            montar(metas_cobertura=None)["METAS_COBERTURA"]
        )

    def test_ausencia_nao_inventa_a_chave(self):
        # Chave ausente deixa o motor usar o proprio default. Inventar um aqui faria
        # o mesmo pedido dar planos diferentes na tela e no notebook.
        assert "METAS_COBERTURA" not in montar()


class TestRepasseDireto:
    @pytest.mark.parametrize(
        "campo,chave,valor",
        [
            ("ete_fixo", "ETE_FIXO", True),
            ("peso_cidade", "PESO_CIDADE", {"Cabo Frio": 5}),
            ("data_inicio", "DATA_INICIO", "2027-01"),
            ("curva_adocao", "CURVA_ADOCAO", "linear"),
            ("usar_cts", "USAR_CTS", False),
        ],
    )
    def test_o_que_a_tela_manda_chega_no_params(self, campo, chave, valor):
        assert montar(**{campo: valor})[chave] == valor

    def test_horizonte_e_total_so_existem_quando_fazem_sentido(self):
        # `HORIZONTE_CAPEX` e do modo "valor anual unico"; `ORCAMENTO_TOTAL` so
        # aparece com redistribuicao. Num cronograma simples, nenhum dos dois.
        p = montar()
        assert "HORIZONTE_CAPEX" not in p
        assert "ORCAMENTO_TOTAL" not in p

    def test_valor_anual_unico_traz_o_horizonte(self):
        p = montar_params(
            {"orcamento_anual": 50_000_000, "horizonte_capex": 8},
            unidade_id="uA3",
            usuario="ana@aegea",
        )
        assert p["ORCAMENTO"] == 50_000_000
        assert p["HORIZONTE_CAPEX"] == 8

    def test_redistribuir_trava_a_soma_em_orcamento_total(self):
        p = montar_params(
            {"orcamento": {"2027": 60_000_000, "2028": 40_000_000}, "redistribuir_orcamento": True},
            unidade_id="uA3",
            usuario="ana@aegea",
        )
        assert p["ORCAMENTO_TOTAL"] == 100_000_000
        # Redistribuir achata todo ano no teto (aqui, o pico).
        assert set(p["ORCAMENTO"].values()) == {60_000_000.0}


class TestDataInicio:
    """A tela coleta ANO-MES; o motor le MES-ANO. A conversao mora no dominio."""

    def test_converte_para_tupla_mes_ano(self):
        assert mes_ano("2027-01") == (1, 2027)
        assert mes_ano("2026/06") == (6, 2026)

    def test_vazio_e_ausente_viram_none(self):
        assert mes_ano(None) is None
        assert mes_ano("") is None

    def test_mes_impossivel_falha_alto(self):
        # `"01-2027"` (MM-AAAA, invertido) daria mes 1 e ano 2027 por acidente no
        # primeiro caso, mas `"2027-13"` denuncia. Falhar aqui e melhor que deslocar
        # a janela para um mes que nao existe.
        with pytest.raises(ParametrosInvalidos):
            mes_ano("2027-13")

    def test_formato_estranho_falha_alto(self):
        with pytest.raises(ParametrosInvalidos):
            mes_ano("janeiro de 2027")
