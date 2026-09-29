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


class TestReguaDaCobertura:
    """A regua da cobertura viaja como `UNIDADE_COBERTURA`, e nao vem mais do banco.

    Era `cidade_operacional.unidade_cobertura`, preenchida CIDADE A CIDADE no
    cadastro (migracao 019 a removeu). Nao e dado: e a lente com que se olha o
    mesmo cadastro. E vale para a unidade inteira — duas cidades da mesma unidade
    medidas em moedas diferentes davam uma cobertura que nao soma.

    O SILENCIO QUE ESTES TESTES GUARDAM e o do arquivo inteiro: se a chave nao
    chegasse ao motor, a pessoa trocaria o controle na tela, veria a cobertura
    mudar por outro motivo, e aprenderia uma relacao que nao existe.
    """

    def test_a_chave_viaja_com_o_valor_escolhido(self):
        for valor in ("ligacoes", "economias", "populacao"):
            assert montar(unidade_cobertura=valor)["UNIDADE_COBERTURA"] == valor

    def test_ausente_nao_produz_a_chave(self):
        # Sem a chave o motor usa o proprio default (`ligacoes`) — que e o que
        # 140 das 141 cidades da base tinham. Corpo antigo continua rodando igual.
        assert "UNIDADE_COBERTURA" not in montar()

    @pytest.mark.parametrize("acentuado", ["ligações", "população"])
    def test_o_valor_acentuado_atravessa_e_o_motor_o_normaliza(self, acentuado):
        # O BACKEND NAO NORMALIZA, e e proposital: quem compara e o motor, por
        # PREFIXO (`startswith("pop")`), e duplicar a regra aqui criaria dois
        # lugares para ela divergir. O teste existe para registrar que o valor
        # atravessa inteiro — foi assim que um 'ligações' com acento ficou anos
        # gravado no banco sem nada acusar, quando isto era campo de cadastro.
        assert montar(unidade_cobertura=acentuado)["UNIDADE_COBERTURA"] == acentuado


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

    def test_max_time_s_fixo_em_1000(self):
        assert montar()["MAX_TIME_S"] == 1000

    @pytest.mark.parametrize("valor", [30, 400, 99999])
    def test_corpo_que_ainda_mande_nao_muda_nada(self, valor):
        assert montar(max_time_s=valor)["MAX_TIME_S"] == 1000

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
    """QUAL ETE e nova sai da FICHA; QUE O MODO E FASEADO sai do pedido.

    ETE com terreno e numero de modulos informados e NOVA: entra como PACOTE INICIAL e
    ganha modulos de expansao se a vazao pedir. A que ja existe e expandida em modulos
    conforme a vazao passa da capacidade ociosa. O motor decide qual e qual por ETE, e
    e por isso que `ETE_FIXO` nao viaja.

    `ETE_FASEADA` PASSOU A VIAJAR em 29/09/2026, e o teste mudou de sentido. A regra
    antiga era "quem afirma True e o executor" — e dois executores nao a cumpriam igual:
    o `dev/worker.py` afirmava, o `job_databricks` nao (por regra propria: "chave ausente
    nao vira default do job") e o `ler_banco` defaulta False. O mesmo pedido rodava
    faseado no local e nao-faseado em producao, e sem o modo NINGUEM fatura — 142
    sub-bacias e R$ 744 mi de receita com True, zero e R$ 0,00 com False, medido na uA1.
    """

    @pytest.mark.parametrize("valor", [True, False])
    def test_ETE_FIXO_nao_vira_parametro(self, valor):
        """O modo fixo continua fora: quem decide e a ficha da ETE."""
        assert "ETE_FIXO" not in montar(ete_fixo=valor)
        assert "ETE_FIXO" not in montar(ete_faseada=valor)

    def test_ETE_FASEADA_VIAJA_E_E_SEMPRE_TRUE(self):
        """Afirmado no pedido, para os dois executores lerem a MESMA fonte."""
        assert montar()["ETE_FASEADA"] is True

    @pytest.mark.parametrize("valor", [True, False])
    def test_a_tela_NAO_pode_desligar_o_faseamento(self, valor):
        """Nem mandando `ete_faseada=False` no corpo.

        Sem o modo o motor recusa a receita de toda sub-bacia do sistema, e uma rodada
        com receita zero nao e uma escolha que a tela deva oferecer. Se um dia o produto
        quiser comparar os dois modos, o caminho e passar a aceitar o campo aqui — e
        entao ele ja entra no digest da deduplicacao, porque viaja no pedido.
        """
        assert montar(ete_faseada=valor)["ETE_FASEADA"] is True


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
        """`None` = a data automatica do motor (primeiro ano do CAPEX x dia da
        rodada); so-espacos e o mesmo que vazio."""
        assert mes_ano(None) is None
        assert mes_ano("") is None
        assert mes_ano("   ") is None
        assert mes_ano(" 2027-01 ") == (1, 2027)

    def test_mes_impossivel_falha_alto(self):
        # `"01-2027"` (MM-AAAA, invertido) daria mes 1 e ano 2027 por acidente no
        # primeiro caso, mas `"2027-13"` denuncia. Falhar aqui e melhor que deslocar
        # a janela para um mes que nao existe.
        with pytest.raises(ParametrosInvalidos):
            mes_ano("2027-13")

    def test_formato_estranho_falha_alto(self):
        with pytest.raises(ParametrosInvalidos):
            mes_ano("janeiro de 2027")


class TestCtsNaCobertura:
    """A CTS conta na COBERTURA? A pergunta só existe com a CTS ligada.

    Pedido do dono do produto em 29/09/2026: com `usar_cts` ligada, a tela oferece escolher
    se as ligações novas da CTS contam na cobertura. A receita das ligações da CTS não muda
    — mas a receita TOTAL pode mudar, porque a cobertura alimenta a faixa de paridade, e ele
    decidiu assim para o produto ter uma cobertura realizada em vez de duas.
    """

    def test_viaja_quando_a_cts_esta_ligada(self):
        assert montar(usar_cts=True, cts_na_cobertura=False)["CTS_NA_COBERTURA"] is False
        assert montar(usar_cts=True, cts_na_cobertura=True)["CTS_NA_COBERTURA"] is True

    def test_A_AUSENCIA_E_A_FORMA_COMPATIVEL_e_o_front_conta_com_isso(self):
        """O par do teste do front, deste lado — a revisão do Codex apontou que faltava.

        `ausente = conta` é o contrato com o motor, e é por isso que o front OMITE o
        default em vez de mandar `true` (`simulacao.ts`, `corpoDaRodada`). Sem esta
        asserção aqui, alguém poderia passar a exigir a chave no backend e o front
        continuaria omitindo — e a rodada nasceria sem o recorte que o pedido não pediu.

        Também não pode aparecer por conta própria: ela entra no digest, e uma chave que
        viaja sempre faz um pedido idêntico a uma rodada antiga ter outro digest.
        """
        # O caminho do front hoje: quem não mexeu no botão manda um corpo SEM o campo.
        assert "CTS_NA_COBERTURA" not in montar(usar_cts=True)
        # E o backend continua aceitando `true` explícito — de um cliente antigo, ou de um
        # script. Não é erro; só não é o que o front manda, e por isso não deduplica com
        # rodada anterior à feature.
        assert montar(usar_cts=True, cts_na_cobertura=True)["CTS_NA_COBERTURA"] is True

    def test_NAO_viaja_quando_ninguem_pediu(self):
        """Ausente = conta, que é o comportamento das 127 rodadas já publicadas.

        A chave não pode aparecer por conta própria: ela entra no digest da deduplicação, e
        um campo que viaja sempre faria toda rodada antiga deixar de casar com uma nova
        idêntica — gastando cluster para produzir o mesmo resultado.
        """
        assert "CTS_NA_COBERTURA" not in montar(usar_cts=True)

    def test_SEM_CTS_a_opcao_de_tirar_e_RECUSADA(self):
        """Erro de quem chama, e não silêncio.

        Sem CTS não há nó de coletor, então "tirar a CTS da cobertura" promete um recorte
        que não aconteceu. A tela só oferece o botão com a CTS ligada; um corpo com os dois
        discordando é bug de cliente, e aceitar gravaria um pedido que mente.
        """
        with pytest.raises(ParametrosInvalidos, match="CTS ligada"):
            montar(usar_cts=False, cts_na_cobertura=False)

    def test_SEM_CTS_dizer_que_conta_e_inofensivo(self):
        """`True` sem CTS não promete nada de errado — só não viaja."""
        assert "CTS_NA_COBERTURA" not in montar(usar_cts=False, cts_na_cobertura=True)
