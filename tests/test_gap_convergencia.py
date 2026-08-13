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


def test_o_valor_padrao_e_o_conservador():
    """0,1%, e nao 0,5%.

    Com 0,5% a fase de cobertura pararia aos 24s em vez de 720s, mas abrindo mao
    de 0,37% de cobertura. Como os coeficientes sao inteiros arredondados e as
    fases anteriores ja travaram obrigatorias e metas, planos quase-equivalentes
    podem empatar sob a metrica do solver — entao o padrao comeca apertado.
    """
    assert _worker().GAP_RELATIVO == 0.001
