"""O caminho até a ETE — a regra de grafo, que é a que erra calada.

A topologia é a tabela mais crítica do cadastro, e por um motivo específico: o
motor percorre `jusante` até a lista acabar (`caminho()`, em
`otimizador_capex_v62.py`) e **não verifica que chegou na ETE**. Um caminho
quebrado não levanta erro — ele deixa de somar as obras de transporte daquele
trecho, e o plano sai mais barato e continua plausível. Um ciclo é pior: o laço
tem trava em 200 saltos, então ele não trava, ele repete o mesmo trecho até 200
vezes e infla os requisitos.

Nada disso aparece no resultado como defeito. Por isso a recusa é na gravação.

Estes testes são Python puro, como os de `test_trilha_cadastro.py`: exercitam a
regra de grafo, que é onde a decisão mora. As regras de PERTENCIMENTO (sistema é
desta unidade? componente existe? o sistema já tem ETE?) são consultas, e o
caminho até o banco é coberto pelos smokes contra Postgres de verdade.
"""

import pytest

from app.infra.repositorios.cadastro_escrita import _id_ou_nada, ciclo_ao_ligar


# ---------------------------------------------------------------- sem ciclo
def test_caminho_reto_ate_a_ete_nao_e_ciclo():
    """`1 → 2 → 3 → ETE`, que é a forma normal de um sistema."""
    sistema = {"b1": "b2", "b2": "b3", "b3": "ete", "ete": None}
    assert ciclo_ao_ligar(sistema, "b1", "b2") == []


def test_dois_ramos_no_mesmo_destino_nao_e_ciclo():
    """Vários componentes escoando para o mesmo tronco é topologia legítima —
    é a forma de bacia, e confundi-la com ciclo impediria de montar."""
    sistema = {"b1": "tronco", "b2": "tronco", "tronco": "ete", "ete": None}
    assert ciclo_ao_ligar(sistema, "b3", "tronco") == []


def test_jusante_em_branco_nunca_fecha_ciclo():
    """Caminho ainda não montado é o estado normal durante o cadastro."""
    assert ciclo_ao_ligar({"b1": "b2", "b2": None}, "b1", None) == []


# ---------------------------------------------------------------- com ciclo
def test_volta_direta_e_ciclo():
    assert ciclo_ao_ligar({"b1": "b2", "b2": None}, "b2", "b1") == ["b2", "b1", "b2"]


def test_ciclo_longo_e_encontrado_e_a_volta_e_devolvida_inteira():
    """A mensagem mostra a volta porque é ela que diz qual ligação desfazer."""
    sistema = {"b1": "b2", "b2": "b3", "b3": None}
    assert ciclo_ao_ligar(sistema, "b3", "b1") == ["b3", "b1", "b2", "b3"]


def test_apontar_para_si_mesmo_e_ciclo_de_um_no():
    assert ciclo_ao_ligar({"b1": None}, "b1", "b1") == ["b1", "b1"]


def test_a_ligacao_nova_vence_a_gravada():
    """O passeio é sobre o sistema COM a mudança aplicada, e não sobre o que está
    no banco: validar contra o estado antigo aprovaria justamente a ligação que
    fecha o ciclo."""
    # Gravado: b1 → b2 → nada. Pedido: b2 → b1. Sobre o estado ANTIGO não há
    # ciclo nenhum; sobre o novo, há.
    assert ciclo_ao_ligar({"b1": "b2", "b2": None}, "b2", "b1") != []


def test_ciclo_que_nao_passa_pelo_componente_que_muda_tambem_barra():
    """Entrar num ciclo que já existe à frente também é caminho que não termina.

    Aqui `b2 → b3 → b2` já está fechado no banco, e `b1` está entrando nele. O
    passeio precisa parar por repetição de QUALQUER nó, e não só por voltar ao
    componente de origem — senão este caso andaria para sempre.
    """
    assert ciclo_ao_ligar({"b2": "b3", "b3": "b2"}, "b1", "b2") == ["b2", "b3", "b2"]


# ------------------------------------------------------------ campo em branco
@pytest.mark.parametrize("vazio", [None, "", "   "])
def test_id_em_branco_e_ausencia(vazio):
    """`jusante` vazio é caminho não montado; `sisId` vazio é fora de sistema.

    Tratar `""` como valor faria a validação procurar um componente de id vazio,
    e `sisId: ""` tentaria colocar o componente num sistema chamado `""` em vez de
    tirá-lo de onde está.
    """
    assert _id_ou_nada(vazio) is None


def test_id_preenchido_perde_o_espaco_em_volta():
    assert _id_ou_nada("  d1b13_1_2 ") == "d1b13_1_2"
