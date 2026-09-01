"""O caminho até a ETE — a regra de grafo, que é a que erra calada.

A topologia é a tabela mais crítica do cadastro, e por um motivo específico: o
motor percorre `jusante` até a lista acabar (`caminho()`, em
`otimizadorcapex_v62.py`) e **não verifica que chegou na ETE**. Um caminho
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

from app.dominio.erros import FichaIncompleta, TopologiaInvalida
from app.dominio.topologia import (
    ciclo_ao_ligar,
    id_ou_nada,
    pedido_do_corpo,
    problemas_do_sistema,
    voltas_do_sistema,
)


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
    assert id_ou_nada(vazio) is None


def test_id_preenchido_perde_o_espaco_em_volta():
    assert id_ou_nada("  d1b13_1_2 ") == "d1b13_1_2"


# ===========================================================================
#  O SISTEMA INTEIRO — a mesma regra de grafo, perguntada sobre o desenho final
# ===========================================================================
#
# `ciclo_ao_ligar` responde "esta ligação nova fecharia um ciclo?", que é a
# pergunta de quem grava um componente por vez. Ela obriga o cliente a ordenar o
# envio, e há reorganização para a qual NENHUMA ordem funciona — não porque o
# desenho final seja inválido, mas porque todo caminho até ele passa por um
# estado que é. Estes testes cobrem a outra pergunta: "o desenho que chegou está
# de pé?".


def _sistema_normal():
    """`b1 → b2 → ete`, com a CTS `c1` entrando em `b2`."""
    return {"b1": "b2", "c1": "b2", "b2": "ete", "ete": None}


# ------------------------------------------------------------------- voltas
def test_bacia_inteira_sem_ciclo_nao_tem_volta():
    assert voltas_do_sistema(_sistema_normal()) == []


def test_volta_direta_aparece_uma_vez_so():
    """`b1 → b2 → b1` é alcançável partindo dos dois, e é UM ciclo."""
    assert voltas_do_sistema({"b1": "b2", "b2": "b1"}) == [["b1", "b2", "b1"]]


def test_dois_ciclos_separados_sao_dois_problemas():
    voltas = voltas_do_sistema({"a1": "a2", "a2": "a1", "z1": "z2", "z2": "z1"})
    assert sorted(sorted(set(v)) for v in voltas) == [["a1", "a2"], ["z1", "z2"]]


def test_quem_desemboca_num_ciclo_nao_vira_um_ciclo_a_mais():
    """`b0` entra num ciclo que já existe à frente: o problema é o ciclo, e
    contá-lo uma vez por afluente encheria a tela com a mesma frase."""
    assert voltas_do_sistema({"b0": "b1", "b1": "b2", "b2": "b1"}) == [
        ["b1", "b2", "b1"]
    ]


def test_o_no_ja_fechado_por_um_passeio_anterior_nao_e_reandado():
    """Forma de bacia: todos desembocam no mesmo tronco. Sem a marca de nó
    fechado o passeio refaria o tronco inteiro por afluente."""
    bacia = {f"b{i}": "tronco" for i in range(12)} | {"tronco": "ete", "ete": None}
    assert voltas_do_sistema(bacia) == []


# ----------------------------------------------------- o desenho está de pé?
def _problemas(escoa, **kw):
    return problemas_do_sistema(
        escoa,
        sistema_id=kw.get("sistema_id", "s1"),
        etes=kw.get("etes", {"ete"}),
        ctss=kw.get("ctss", {"c1"}),
        usa_cts=kw.get("usa_cts", False),
    )


def test_desenho_normal_nao_tem_problema():
    assert _problemas(_sistema_normal()) == []


def test_tirar_a_cts_e_reapontar_quem_escoava_para_ela_e_aceito():
    """É O CASO QUE ESTA ROTA VEIO RESOLVER.

    A `c1` sai do sistema e a `b1`, que escoava para ela, passa a escoar para a
    `b2`. O desenho final está perfeito. Gravando um componente por vez ele era
    recusado nas duas ordens possíveis: tirar a `c1` primeiro esbarra na `b1`
    ainda apontando para ela, e o cliente não tem como saber que precisa mandar a
    `b1` antes — foi exatamente o erro que o usuário viu na tela.
    """
    antes = {"b1": "c1", "c1": "b2", "b2": "ete", "ete": None}
    assert voltas_do_sistema(antes) == []  # o de partida também era válido
    depois = {"b1": "b2", "b2": "ete", "ete": None}  # sem a `c1`
    assert _problemas(depois) == []


def test_inverter_um_trecho_e_aceito():
    """`b1 → b2` vira `b2 → b1`. Um componente por vez, a ligação nova sozinha
    fecha um ciclo contra o banco; sobre o desenho final não há ciclo nenhum."""
    assert _problemas({"b2": "b1", "b1": "ete", "ete": None}) == []


def test_jusante_fora_do_sistema_e_recusado():
    """Cobre os dois casos numa frase só: componente de outro sistema, e
    componente que acabou de sair deste."""
    (problema,) = _problemas({"b1": "de_outro", "ete": None})
    assert "'b1'" in problema and "'de_outro'" in problema and "'s1'" in problema


def test_escoar_para_si_mesmo_e_recusado():
    (problema,) = _problemas({"b1": "b1", "ete": None})
    assert "si mesmo" in problema


def test_ciclo_no_desenho_final_e_recusado_e_mostra_a_volta():
    (problema,) = _problemas({"b1": "b2", "b2": "b1"}, etes=set())
    assert "b1 → b2 → b1" in problema


def test_ete_com_jusante_e_recusada():
    """A ETE é o fim do caminho: na base inteira não há uma com jusante."""
    (problema,) = _problemas({"ete": "b1", "b1": None})
    assert "'ete'" in problema and "fim do caminho" in problema


def test_duas_etes_no_mesmo_sistema_sao_recusadas():
    """O motor guarda UMA ETE por sistema (`ete_do_sis[sis] = comp`): a segunda
    sobrescreve a primeira em silêncio."""
    (problema,) = _problemas(
        {"ete": None, "ete2": None}, etes={"ete", "ete2"}
    )
    assert "'ete'" in problema and "'ete2'" in problema and "uma ETE só" in problema


def test_duas_cts_so_incomodam_quando_o_sistema_diz_que_e_de_cts():
    dois_cts = {"c1": "ete", "c2": "ete", "ete": None}
    assert _problemas(dois_cts, ctss={"c1", "c2"}, usa_cts=False) == []
    (problema,) = _problemas(dois_cts, ctss={"c1", "c2"}, usa_cts=True)
    assert "'c1'" in problema and "'c2'" in problema


def test_todos_os_problemas_voltam_juntos():
    """Um por gravação transformaria a correção numa fila de tentativas — que é o
    que esta rota veio desfazer."""
    problemas = _problemas(
        {"b1": "b1", "b2": "de_fora", "b3": "b4", "b4": "b3", "ete": "b1"}
    )
    assert len(problemas) == 4


def test_sistema_vazio_nao_tem_problema():
    """Esvaziar um sistema é legítimo — e é o estado de quem vai remontá-lo."""
    assert _problemas({}) == []


def test_caminho_ainda_nao_montado_nao_e_problema_de_forma():
    """Origem sem destino é o estado normal durante o cadastro, e a tela já avisa
    disso no painel da aba. Recusar a gravação impediria de salvar no meio."""
    assert _problemas({"b1": None, "b2": None, "ete": None}) == []


# ------------------------------------------------------------ forma do corpo
def test_corpo_vira_mapa_por_sistema_com_vazio_como_ausencia():
    pedido = pedido_do_corpo(
        {
            "sistemas": [
                {
                    "id": " s1 ",
                    "componentes": [
                        {"id": "b1", "jusante": "ete"},
                        {"id": "ete", "jusante": ""},
                    ],
                }
            ]
        }
    )
    assert pedido == {"s1": {"b1": "ete", "ete": None}}


def test_lista_vazia_de_componentes_esvazia_o_sistema():
    assert pedido_do_corpo({"sistemas": [{"id": "s1", "componentes": []}]}) == {"s1": {}}


def test_componentes_ausente_e_corpo_incompleto_e_nao_sistema_vazio():
    """A diferença importa: aceitar a chave ausente esvaziaria o sistema por
    engano, e o cliente com um bug apagaria o desenho inteiro sem sinal."""
    with pytest.raises(FichaIncompleta):
        pedido_do_corpo({"sistemas": [{"id": "s1"}]})


def test_o_mesmo_componente_em_dois_sistemas_do_envio_e_recusado():
    """Sem esta recusa o segundo bloco venceria em silêncio, e o sistema que o
    pediu primeiro ficaria sem ele."""
    with pytest.raises(TopologiaInvalida, match="dois sistemas"):
        pedido_do_corpo(
            {
                "sistemas": [
                    {"id": "s1", "componentes": [{"id": "b1", "jusante": ""}]},
                    {"id": "s2", "componentes": [{"id": "b1", "jusante": ""}]},
                ]
            }
        )


def test_o_mesmo_sistema_duas_vezes_no_corpo_e_recusado():
    with pytest.raises(FichaIncompleta, match="duas vezes"):
        pedido_do_corpo(
            {
                "sistemas": [
                    {"id": "s1", "componentes": []},
                    {"id": "s1", "componentes": [{"id": "b1", "jusante": ""}]},
                ]
            }
        )


def test_corpo_sem_sistemas_e_recusado():
    for corpo in ({}, {"sistemas": []}, {"sistemas": "s1"}):
        with pytest.raises(FichaIncompleta):
            pedido_do_corpo(corpo)
