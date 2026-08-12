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


class TestExecucao:
    """Tempo de solver e paralelismo nao sao decisao de quem dispara a rodada."""

    def test_max_time_s_fixo_em_5000(self):
        assert montar()["MAX_TIME_S"] == 5000

    @pytest.mark.parametrize("valor", [30, 400, 99999])
    def test_corpo_que_ainda_mande_nao_muda_nada(self, valor):
        assert montar(max_time_s=valor)["MAX_TIME_S"] == 5000

    def test_workers_nao_viaja(self):
        # Paralelismo depende da maquina que executa; o executor usa o proprio
        # padrao. Fixar aqui seria decidir por uma maquina que nao conhecemos.
        assert "WORKERS" not in montar()
        assert "WORKERS" not in montar(workers=16)


class TestPesoCidade:
    """Sem parametro: todas as cidades pesam 1.

    A ausencia E o padrao pedido — o motor multiplica por
    `peso_cidade.get(cidade, 1.0)`. Mandar `{}` daria no mesmo e sugeriria escolha.
    E o caso oposto ao `ANOS_EXTRA_CONCLUSAO`, onde o default do motor (3) nao era
    o que se queria e o valor precisou ser afirmado.
    """

    def test_nunca_produz_a_chave(self):
        assert "PESO_CIDADE" not in montar()

    @pytest.mark.parametrize("valor", [{}, {"Cabo Frio": 5}])
    def test_corpo_que_ainda_mande_e_ignorado(self, valor):
        assert "PESO_CIDADE" not in montar(peso_cidade=valor)


class TestAnosExtraConclusao:
    """Fixo em ZERO: a obra inicia e conclui dentro da janela de CAPEX.

    O valor e AFIRMADO, e nao omitido. O default do motor e 3 — chave ausente daria
    tres anos de rabo, que e o oposto do pedido. E o espelho do `ete_faseada`: la a
    omissao desligaria o que se quer, aqui ligaria o que nao se quer.
    """

    def test_sempre_zero(self):
        assert montar()["ANOS_EXTRA_CONCLUSAO"] == 0

    def test_a_chave_existe_sempre(self):
        # Ela precisa VIAJAR: e assim que o historico registra o que a rodada usou,
        # e que o modal de detalhes consegue mostra-lo.
        assert "ANOS_EXTRA_CONCLUSAO" in montar()

    @pytest.mark.parametrize("valor", [3, 5, 0, None])
    def test_corpo_que_ainda_mande_nao_muda_nada(self, valor):
        # Cliente antigo pode mandar o campo. O zero e regra do produto, nao
        # sugestao — entao ele ganha de qualquer valor que chegue.
        assert montar(anos_extra_conclusao=valor)["ANOS_EXTRA_CONCLUSAO"] == 0


class TestEte:
    """O tratamento da ETE sai da FICHA dela, e nao da rodada.

    ETE com terreno e numero de modulos informados e NOVA: entra como pacote unico,
    sem faseamento. A que ja existe e expandida em modulos conforme a vazao passa da
    capacidade ociosa. O motor decide isso por ETE.

    CUIDADO: aqui a receita das metas nao se aplica. La, apagar o parametro dava o
    comportamento certo porque o default do motor ja era ele. Aqui o default de
    `ete_faseada` e False — a chave sumir do `params` esta certo, mas quem executa
    tem de AFIRMAR True. Ver `dev/worker.py`.
    """

    @pytest.mark.parametrize("campo", ["ete_faseada", "ete_fixo"])
    @pytest.mark.parametrize("valor", [True, False])
    def test_nao_viram_parametro(self, campo, valor):
        assert "ETE_FASEADA" not in montar(**{campo: valor})
        assert "ETE_FIXO" not in montar(**{campo: valor})


class TestRepasseDireto:
    @pytest.mark.parametrize(
        "campo,chave,valor",
        [
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
