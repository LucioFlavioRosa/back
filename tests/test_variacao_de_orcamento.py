"""OS DOIS MODOS DA ANÁLISE DE SENSIBILIDADE, e o contrato que os separa.

A regra de produto que estes testes prendem, em uma frase: **a rodada que a
pessoa manda rodar é uma simulação normal; a análise de sensibilidade dela são
cinco variações em modo rápido.**

O que erraria em silêncio, e é por isso que cada um destes existe:

- uma estimativa de 60s ser devolvida a quem pediu a simulação completa (ou o
  contrário) pela deduplicação — os dois números são plausíveis, e a tela não
  teria como saber qual recebeu;
- o orçamento escalado acumular resíduo de ponto flutuante e a dedupe parar de
  reconhecer o mesmo pedido, gastando cluster duas vezes pela mesma resposta;
- `ORCAMENTO_TOTAL` ficar com o valor antigo enquanto os anos sobem, e o motor
  recusar o plano que ele mesmo acabou de receber.
"""

import pytest

from app.dominio.parametros import ParametrosInvalidos, montar_params
from app.dominio.variacao import (
    SEGUNDOS_MAXIMOS_DA_ESTIMATIVA,
    SEGUNDOS_MINIMOS_DA_ESTIMATIVA,
    params_da_variacao,
    segundos_da_estimativa,
)

BASE = {
    "ORCAMENTO": {"2027": 60_000_000.0, "2028": 50_000_700.0},
    "BASE_RECEITA": "arrecadada",
    "USAR_CTS": True,
}


def variar(fator=1.1, modo="rapido", base=None, colunas=None):
    return params_da_variacao(
        base if base is not None else BASE,
        unidade_id="uB2",
        usuario="lucio",
        fator=fator,
        modo=modo,
        colunas=colunas,
    )


class TestOsDoisModos:
    def test_a_rodada_padrao_roda_com_o_tempo_de_solver_de_sempre(self):
        # `POST /runs` — a simulação que a pessoa manda rodar. 1000s, e o cliente
        # não escolhe: quanto tempo o solver tem é afinação de execução, não
        # decisão de negócio.
        padrao = montar_params(
            {"orcamento": {"2027": 60_000_000}}, unidade_id="uB2", usuario="lucio"
        )
        assert padrao["MAX_TIME_S"] == 1000
        # E a estimativa é MENOR que ela — a ordem entre as duas é o contrato.
        assert variar(modo="rapido")["MAX_TIME_S"] < padrao["MAX_TIME_S"]

    def test_a_estimativa_corta_o_solver(self):
        assert variar(modo="rapido")["MAX_TIME_S"] == SEGUNDOS_MINIMOS_DA_ESTIMATIVA == 60

    def test_a_variacao_completa_nao_impoe_teto_proprio(self):
        # Sem a chave, ela cai no default do consumidor — que para o job é o
        # mesmo 1000s da rodada normal. É o que a torna comparável com as outras
        # do histórico, e é por isso que ela aparece lá.
        assert "MAX_TIME_S" not in variar(modo="completo")

    def test_estimativa_e_simulacao_da_MESMA_variacao_sao_pedidos_DIFERENTES(self):
        # É o que impede a dedupe de trocar uma pela outra. Sem esta diferença,
        # quem pedisse a simulação de 1000s receberia a estimativa de 60s já
        # existente, com um VPL menor, e nada na tela diria que houve troca.
        assert variar(modo="rapido") != variar(modo="completo")

    def test_o_modo_desconhecido_e_recusado(self):
        with pytest.raises(ParametrosInvalidos):
            variar(modo="turbo")


class TestOEscalonamentoDoOrcamento:
    def test_escala_CADA_ANO_pelo_fator(self):
        p = variar(fator=1.1)["ORCAMENTO"]
        assert p == {"2027": 66_000_000.0, "2028": 55_000_770.0}

    def test_arredonda_a_centavos_para_a_dedupe_continuar_reconhecendo(self):
        # `60000000 * 1.1` dá 66000000.00000001 em ponto flutuante. O resíduo
        # entraria no digest, e a mesma análise pedida duas vezes gastaria
        # cluster duas vezes.
        bruto = 60_000_000.0 * 1.1
        assert bruto != 66_000_000.0  # o resíduo existe mesmo
        assert variar(fator=1.1)["ORCAMENTO"]["2027"] == 66_000_000.0

    def test_orcamento_anual_unico_tambem_escala(self):
        p = variar(fator=1.5, base={"ORCAMENTO": 80_000_000.0})
        assert p["ORCAMENTO"] == 120_000_000.0

    def test_ORCAMENTO_TOTAL_sobe_junto(self):
        # Deixá-lo como estava faria o teto novo brigar com um total antigo.
        p = variar(fator=1.2, base={**BASE, "ORCAMENTO_TOTAL": 110_000_700.0})
        assert p["ORCAMENTO_TOTAL"] == pytest.approx(132_000_840.0)

    def test_sem_ORCAMENTO_TOTAL_a_chave_nao_e_inventada(self):
        assert "ORCAMENTO_TOTAL" not in variar()

    def test_rodada_sem_orcamento_gravado_e_recusada(self):
        with pytest.raises(ParametrosInvalidos):
            variar(base={"BASE_RECEITA": "arrecadada"})

    @pytest.mark.parametrize("ruim", [0, -1, "1.1", None, True])
    def test_fator_que_nao_e_numero_positivo_e_recusado(self, ruim):
        # `True` entra na lista de propósito: em Python ele É um int, e
        # `bool * float` daria 1.0 — a variação viraria uma cópia da rodada base
        # com nome de análise.
        with pytest.raises(ParametrosInvalidos):
            variar(fator=ruim)


class TestOQueNaoMuda:
    def test_todo_o_resto_dos_parametros_vem_intacto(self):
        # É o que faz a comparação medir o efeito do ORÇAMENTO. Mexer em duas
        # coisas mediria a diferença entre duas simulações quaisquer.
        p = variar()
        assert p["BASE_RECEITA"] == "arrecadada"
        assert p["USAR_CTS"] is True

    def test_a_variacao_nasce_assinada_por_quem_a_pediu(self):
        # E não pelo autor da rodada de origem: a curva é de quem a está
        # analisando, e a posse das rodadas é por pessoa.
        assert variar()["USUARIO"] == "lucio"
        assert variar()["UNIDADE"] == "uB2"

    def test_nao_altera_os_parametros_de_origem(self):
        antes = {"ORCAMENTO": {"2027": 60_000_000.0}}
        copia = {"ORCAMENTO": dict(antes["ORCAMENTO"])}
        variar(base=antes)
        assert antes == copia


class TestOTempoDaEstimativa:
    """60s FIXO ERA O DEFEITO, e ele custou duas rodadas mortas na maior unidade.

    Dar o mesmo orçamento de relógio a um modelo seis vezes maior não é "rápido",
    é insuficiente — e insuficiente no motor não devolve um plano pior: MATA a
    rodada, uma vez com `KeyError` no reparo do teto anual e outra com falha
    nativa do processo.
    """

    def test_cresce_com_o_tamanho_do_modelo(self):
        pequeno = segundos_da_estimativa(12_111)   # uB2
        medio = segundos_da_estimativa(25_180)     # uA2
        grande = segundos_da_estimativa(72_711)    # uA3
        assert pequeno < medio < grande

    def test_a_maior_unidade_medida_recebe_o_que_se_sabe_que_basta(self):
        # A uA3 falhou duas vezes com 60s e concluiu com 500s (medição de 13/08,
        # registrada em `dominio/parametros.py`). A constante é calibrada por ela.
        assert segundos_da_estimativa(72_711) == 500

    def test_nunca_abaixo_do_piso(self):
        # Abaixo de 60s o motor pula a terceira fase, e a "estimativa" deixa de
        # ser a mesma otimização com menos relógio.
        assert segundos_da_estimativa(1) == SEGUNDOS_MINIMOS_DA_ESTIMATIVA
        assert segundos_da_estimativa(0) == SEGUNDOS_MINIMOS_DA_ESTIMATIVA
        assert segundos_da_estimativa(None) == SEGUNDOS_MINIMOS_DA_ESTIMATIVA

    def test_para_em_500s(self):
        # Medido na maior unidade: 500s basta (11m57s no total) e abaixo disso
        # não fica mais rápido — 180s levou 16m14s na MESMA rodada, porque
        # `MAX_TIME_S` é teto POR SOLVE e não relógio da rodada.
        assert segundos_da_estimativa(10_000_000) == SEGUNDOS_MAXIMOS_DA_ESTIMATIVA == 500
        assert segundos_da_estimativa(72_711) == 500

    def test_o_modo_completo_ignora_o_tamanho(self):
        # Ele não impõe teto nenhum: cai no default do consumidor, que é o mesmo
        # 1000s da rodada normal.
        assert "MAX_TIME_S" not in variar(modo="completo", colunas=72_711)

    def test_sem_tamanho_conhecido_cai_no_piso(self):
        # Rodada que não publicou obras/anos. Comportamento de antes, e não um
        # erro: o piso é seguro para as unidades em que ele foi observado.
        assert variar(modo="rapido", colunas=None)["MAX_TIME_S"] == 60
