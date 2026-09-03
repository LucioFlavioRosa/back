"""A DISTRIBUIÇÃO DAS OBRAS QUE FICARAM FORA, ano a ano.

Era um rateio — o total de cada componente vezes o peso do ano. Dava barras
certas com uma mentira embaixo: nenhuma obra pertencia a ano nenhum, então "os
troncos de 2029" não existiam para listar nem para baixar. Agora cada obra cai
em UM ano, e é isso que faz a planilha de uma fatia ser exatamente o que aquela
fatia soma.

Sem banco: `_distribuir` é pura, e é justamente por ser pura que a regra pode
ser afirmada aqui em vez de só no e2e.
"""

import pytest

from app.infra.repositorios.explicabilidade import _distribuir

JANELA = [(2027, 60e6), (2028, 40e6), (2029, 40e6), (2030, 20e6)]


def _obras(quantos: int, capex: float = 1e6) -> list[dict]:
    return [{"obra_id": f"o{i}", "capex": capex} for i in range(quantos)]


def test_cada_obra_cai_em_exatamente_um_ano():
    # É A DIFERENÇA PARA O RATEIO. Uma obra em dois anos seria contada duas
    # vezes na soma da janela; em nenhum, sumiria da tela sem aviso.
    obras = _obras(50)
    onde = _distribuir(obras, JANELA)

    assert len(onde) == 50
    assert set(onde.values()) <= {a for a, _ in JANELA}


def test_a_soma_dos_anos_e_a_soma_das_obras():
    obras = _obras(37, capex=2.5e6)
    onde = _distribuir(obras, JANELA)

    por_ano = {a: 0.0 for a, _ in JANELA}
    for o in obras:
        por_ano[onde[o["obra_id"]]] += o["capex"]

    assert sum(por_ano.values()) == pytest.approx(sum(o["capex"] for o in obras))


def test_os_anos_seguem_o_perfil_do_orcamento():
    # A ESCALA É MAIOR, A FORMA É A MESMA: o ano que hoje tem 60 de 160 fica com
    # perto de 3/8 do que falta. Não é otimização — é a cota do ano, e a tela
    # diz isso. A folga de 5 pontos existe porque obra não se parte ao meio.
    obras = _obras(400)
    onde = _distribuir(obras, JANELA)

    total = sum(o["capex"] for o in obras)
    for ano, orcado in JANELA:
        parte = sum(o["capex"] for o in obras if onde[o["obra_id"]] == ano) / total
        esperado = orcado / sum(v for _, v in JANELA)
        assert abs(parte - esperado) < 0.05


def test_uma_obra_gigante_nao_estoura_a_janela_nem_some():
    # O CASO QUE QUEBRA UM RATEIO INGÊNUO: uma obra maior que a cota de qualquer
    # ano. Ela tem de cair inteira em algum ano — o mais vazio —, e não ser
    # partida nem descartada por não caber.
    obras = [{"obra_id": "gigante", "capex": 500e6}, *_obras(3)]
    onde = _distribuir(obras, JANELA)

    assert onde["gigante"] == 2027  # o ano de maior cota, que é o mais vazio
    assert len(onde) == 4


def test_sem_obra_nenhuma_a_distribuicao_e_vazia():
    # "Só o que se paga" pode não ter nada em uma rodada pequena, e o cenário
    # ainda tem de responder — com uma barra zerada, não com uma exceção.
    assert _distribuir([], JANELA) == {}


@pytest.mark.asyncio
async def test_cidade_e_sistema_juntos_sao_recusados():
    """OS DOIS RECORTES NÃO SE COMPÕEM, e a recusa é explícita.

    A consulta principal filtraria os dois, mas a de elos só aplica o primeiro —
    a tela sairia com os números de cima e a lista de baixo falando de conjuntos
    diferentes, sem nada acusar. As rotas de hoje nunca passam os dois; quem
    chamar direto tem de esbarrar, e não receber um resultado plausível.
    """
    from app.infra.repositorios.explicabilidade import explicabilidade

    with pytest.raises(ValueError, match="ao mesmo tempo"):
        await explicabilidade("run_x", cidade="Iguaba", sistema="Sistema 64")
