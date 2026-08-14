"""O contorno que faz o solver parar quando ja esta perto do otimo.

Sem criterio de convergencia o CP-SAT so para por prova exata ou por relogio. Na
unidade de 67 cidades isso custou 339s (47% do tempo de solver) DEPOIS da ultima
melhoria reportada, para devolver um plano que ja estava a 0,006% do limite
superior provado — e ainda reportar VIAVEL(limite de tempo), que se le como
"faltou tempo".

Isto e CONTORNO, nao solucao: o certo e o motor receber o gap como parametro
(`dev/patches/motor_criterio_de_convergencia.md`). O que os testes aqui protegem
e o que o contorno PROMETE — que ele age, que ele nao afrouxa nada, e sobretudo
que ele REVERTE. O processo do pool e reusado entre rodadas; um patch que
vazasse continuaria valendo para as seguintes, e ninguem veria.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))

cp_model = pytest.importorskip("ortools.sat.python.cp_model")


def _worker():
    """Importa `dev/worker.py` sem subir fila nem banco.

    `sys.argv` fica vazio durante o import porque `rodar_simulacao_real`, que o
    worker importa, le a linha de comando NO IMPORT — com os argumentos do pytest
    ele estoura em `int(sys.argv[2])`.
    """
    import importlib

    guardado, sys.argv = sys.argv, [sys.argv[0]]
    try:
        return importlib.import_module("worker")
    finally:
        sys.argv = guardado


def _resolver_trivial(esperado: float) -> None:
    """Resolve um modelo de um passo, conferindo o gap que o solver recebeu."""
    m = cp_model.CpModel()
    x = m.NewBoolVar("x")
    m.Maximize(x)
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = 5.0
    s.Solve(m)
    assert s.parameters.relative_gap_limit == pytest.approx(esperado)


def test_o_gap_chega_ao_solver():
    w = _worker()
    with w.gap_de_convergencia(w.GAP_RELATIVO):
        _resolver_trivial(w.GAP_RELATIVO)


def test_reverte_ao_sair__o_processo_do_pool_e_reusado():
    w = _worker()
    original = cp_model.CpSolver.Solve
    with w.gap_de_convergencia(0.01):
        assert cp_model.CpSolver.Solve is not original
    assert cp_model.CpSolver.Solve is original
    _resolver_trivial(0.0)  # a proxima rodada comeca limpa


def test_reverte_mesmo_quando_a_rodada_estoura():
    w = _worker()
    original = cp_model.CpSolver.Solve
    with pytest.raises(KeyError):
        with w.gap_de_convergencia(0.01):
            raise KeyError("Araruama Leste1")
    assert cp_model.CpSolver.Solve is original


def test_nao_afrouxa_um_gap_mais_rigoroso_do_motor():
    """No dia em que o motor definir o proprio gap, vence o mais rigoroso.

    Nos dois sentidos o erro cai para o lado do plano melhor: nunca afrouxamos o
    dele, e se o dele for mais frouxo o nosso prevalece.
    """
    w = _worker()
    m = cp_model.CpModel()
    m.Maximize(m.NewBoolVar("x"))
    with w.gap_de_convergencia(0.01):
        s = cp_model.CpSolver()
        s.parameters.relative_gap_limit = 0.0001      # o motor pediu mais rigor
        s.Solve(m)
        assert s.parameters.relative_gap_limit == pytest.approx(0.0001)

        s2 = cp_model.CpSolver()
        s2.parameters.relative_gap_limit = 0.5        # o motor pediu menos
        s2.Solve(m)
        assert s2.parameters.relative_gap_limit == pytest.approx(0.01)


def test_a_cobertura_e_apertada_e_o_retorno_e_folgado():
    """A ASSIMETRIA e o ponto, e o teste existe para ela nao se perder num ajuste.

    A medicao desfez a hipotese inicial. Com folga unica de 2%, duas execucoes
    deram `C*` de 670.092 e 670.193 — 0,015% de diferenca — e mesmo assim VPL de
    181,70 e 175,02 Mi, 3,7% de diferenca. Variacao de 0,015% na restricao nao
    causa 3,7% no resultado: a dispersao vem da FASE 3, nao do `C*`.

    Por isso a cobertura fica em 2%, que e barato (o `C*` variou 0,46% no pior caso
    entre tres execucoes), e o retorno em 5%, escolha de produto pela velocidade.

    O QUE ESTE TESTE PROTEGE nao e o par de numeros — e a relacao entre eles. Se
    alguem igualar os dois, a separacao inteira perde sentido: seria voltar ao botao
    unico que governava duas moedas diferentes.
    """
    w = _worker()
    assert w.GAP_RELATIVO == 0.02
    assert w.GAP_RETORNO == 0.05
    assert w.GAP_RETORNO > w.GAP_RELATIVO


def test_escolhe_o_parametro_nativo_quando_o_motor_o_tem():
    """A escolha entre o parametro do motor e o contorno e por INSPECAO, nao por fe.

    Os dois lados andam em cadencias diferentes: o motor do job Databricks e o
    pacote que a maquina local carrega nao sobem juntos. Passar `gap_relativo`
    para um motor antigo da `TypeError` e derruba a rodada; presumir o contrario
    deixa o remendo por fora ativo sobre um motor que ja faz certo — e esse erro e
    silencioso, que e o pior dos dois.
    """
    w = _worker()

    class MotorComDois:
        @staticmethod
        def resolver_por_sistema(cen, max_time_s=60, workers=8, gap_relativo=0.0,
                                 gap_retorno=None): ...

    class MotorComUm:
        @staticmethod
        def resolver_por_sistema(cen, max_time_s=60, workers=8, gap_relativo=0.0): ...

    class MotorAntigo:
        @staticmethod
        def resolver_por_sistema(cen, max_time_s=60, workers=8): ...

    # Tres geracoes em circulacao, e cada uma exige uma chamada diferente.
    assert "gap_retorno" in w._parametros_do_motor(MotorComDois)
    assert "gap_retorno" not in w._parametros_do_motor(MotorComUm)
    assert "gap_relativo" in w._parametros_do_motor(MotorComUm)
    assert "gap_relativo" not in w._parametros_do_motor(MotorAntigo)


def test_motor_opaco_nao_recebe_chave_nenhuma():
    """Sem assinatura legivel, NADA de gap: na duvida vale o contorno, que funciona
    nos tres mundos, enquanto o parametro so funciona em dois.

    O que importa nao e o conjunto vir vazio — e nenhuma chave de gap aparecer
    nele. Um builtin como `len` TEM assinatura (`(obj, /)`), entao exigir vazio
    testaria o detalhe errado.
    """
    w = _worker()

    class MotorOpaco:
        resolver_por_sistema = object()   # `inspect.signature` levanta TypeError

    class MotorBuiltin:
        resolver_por_sistema = len        # tem assinatura, mas nao a nossa

    for motor in (MotorOpaco, MotorBuiltin):
        aceita = w._parametros_do_motor(motor)
        assert "gap_relativo" not in aceita and "gap_retorno" not in aceita
