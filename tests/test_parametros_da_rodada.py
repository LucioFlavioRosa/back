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
    """A fonte das metas NAO e parametro da rodada: e sempre a base.

    O unico descarte legitimo e por ANO — meta fora da janela de CAPEX nao e
    cobrada —, e quem aplica isso e o motor, na avaliacao. Nao ha o que escolher
    aqui, entao a chave nao e produzida: sem ela o motor usa o default, que e
    carregar da planilha.
    """

    def test_nunca_produz_a_chave(self):
        assert "METAS_COBERTURA" not in montar()

    @pytest.mark.parametrize("valor", ["cadastro", None, {"Cabo Frio": {2030: 0.9}}])
    def test_corpo_que_ainda_mande_metas_e_ignorado(self, valor):
        # Cliente antigo — ou alguem chamando a API na mao — pode mandar o campo.
        # Ignorar em silencio da o resultado que a regra pede; recusar quebraria a
        # tela velha sem beneficio, ja que o comportamento final e o mesmo.
        assert "METAS_COBERTURA" not in montar(metas_cobertura=valor)


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
