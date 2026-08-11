"""As frases que a tela mostra sobre a fila, com a concordancia certa.

Elas sao montadas AQUI, e nao no front, porque so o servidor ve a fila inteira —
entao a qualidade do texto tambem e responsabilidade daqui. "Todas as 1 vagas
estao ocupadas" foi visto em tela: e o tipo de descuido que faz o usuario duvidar
do resto dos numeros, num painel que existe para ser confiavel sobre o que esta
acontecendo.
"""

from app.api.simulacao import _plural, _todas_ocupadas


def test_singular_nao_vira_plural():
    assert _plural(1, "vaga livre", "vagas livres") == "1 vaga livre"
    assert _plural(1, "simulação", "simulações") == "1 simulação"


def test_plural_a_partir_de_dois():
    assert _plural(2, "vaga livre", "vagas livres") == "2 vagas livres"
    assert _plural(4, "simulação", "simulações") == "4 simulações"


def test_zero_e_plural():
    # "0 vaga livre" leria pior que "0 vagas livres", e este ramo so aparece na
    # contagem de quem esta na frente na fila.
    assert _plural(0, "simulação", "simulações") == "0 simulações"


def test_uma_vaga_nao_tem_todas():
    # Era o caso real: um executor com `--paralelo 1`. "Todas as 1 vagas estao
    # ocupadas" erra o "todas" e o plural na mesma frase.
    assert _todas_ocupadas(1) == "A única vaga está ocupada."


def test_varias_vagas_mantem_a_frase_de_sempre():
    assert _todas_ocupadas(4) == "Todas as 4 vagas estão ocupadas."
