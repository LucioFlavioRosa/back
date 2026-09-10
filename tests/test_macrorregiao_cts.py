# -*- coding: utf-8 -*-
"""A regua que funde varias CTS numa, quando a unidade usa macrorregiao.

Os numeros destes testes NAO sao inventados: saem dos grupos que existem no banco
de desenvolvimento, medidos no dump de 03/09. `e1s27` e `e2s83` sao dois dos 32
grupos reais, e sao justamente os que mostram por que somar tudo seria errado.
"""

import pytest

from app.dominio import campos
from app.dominio.macrorregiao_cts import (
    COLUNAS_DA_REGIONAL,
    COLUNAS_QUE_SOMAM,
    agregar,
    COLUNAS_COMPARAVEIS,
    agrupar,
    divergencias,
    livres,
    nomes_ambiguos,
)


def cts(**kw):
    """Uma ficha de CTS com o minimo: cidade e o que o teste quiser."""
    return {"cidade_id": "c1", **kw}


def test_as_medidas_do_databricks_somam():
    fora = agregar([
        cts(receita_arrecadada_media_mensal=1000.0, ligacoes_atuais=40, universo_ligacoes=90),
        cts(receita_arrecadada_media_mensal=1500.0, ligacoes_atuais=60, universo_ligacoes=110),
    ])
    assert fora["receita_arrecadada_media_mensal"] == pytest.approx(2500.0)
    assert fora["ligacoes_atuais"] == 100
    assert fora["universo_ligacoes"] == 200


def test_o_que_a_regional_preenche_nao_entra_no_resultado():
    """`e1s27` real: somar `tempo_arrecadacao` daria 16, onde o maior e 12.

    A funcao nao decide entre soma, moda ou maximo — ela simplesmente NAO devolve
    a coluna, porque quem a informa e a Regional, na macrorregiao.
    """
    fora = agregar([
        cts(preco_por_ligacao=1284.51, tempo_arrecadacao=12, potencial_crescimento=1.15),
        cts(preco_por_ligacao=1336.59, tempo_arrecadacao=4, potencial_crescimento=1.15),
    ])
    for coluna in COLUNAS_DA_REGIONAL:
        assert coluna not in fora, f"{coluna} e preenchimento da Regional, nao pode agregar"


def test_os_dois_blocos_nao_se_cruzam():
    """Uma coluna nao pode somar E ser preenchida. A divisao vem de `campos.py`."""
    assert not (set(COLUNAS_QUE_SOMAM) & set(COLUNAS_DA_REGIONAL))


def test_o_recorte_residencial_soma_entre_membros():
    """Somar residencial ENTRE CTS e certo; soma-lo ao total e que nunca."""
    fora = agregar([
        cts(ligacoes_atuais=40, ligacoes_atuais_residencial=30),
        cts(ligacoes_atuais=60, ligacoes_atuais_residencial=50),
    ])
    assert fora["ligacoes_atuais_residencial"] == 80
    assert fora["ligacoes_atuais"] == 100, "o total nao absorve o recorte"


def test_ausencia_nao_vira_zero():
    """Uma CTS sem receita informada nao afirma receita nula.

    Somar zero por ela puxaria o total para baixo com um numero que ninguem
    mediu — e a regra do servico e que `null` significa "nao existe", nunca 0.
    """
    fora = agregar([
        cts(receita_faturada_media_mensal=800.0),
        cts(receita_faturada_media_mensal=None),
    ])
    assert fora["receita_faturada_media_mensal"] == pytest.approx(800.0)


def test_coluna_que_ninguem_mediu_sai_nula():
    """A coluna testada tem de ser a coluna PASSADA — senao o teste prova outra coisa.

    Estava passando `universo_populacao=None` (que nem agrega) e conferindo
    `populacao_novas_obras` (que nenhum membro trazia). Passava por chave ausente,
    e nao pela regra.
    """
    fora = agregar([
        cts(populacao_novas_obras=None, ligacoes_atuais=10),
        cts(populacao_novas_obras=None, ligacoes_atuais=20),
    ])
    assert fora["populacao_novas_obras"] is None
    assert fora["ligacoes_atuais"] == 30, "a coluna medida ao lado continua somando"


def test_populacao_novas_obras_soma_mesmo_sem_estar_na_tela():
    """Ela nao e modelada pelo front e por isso e a mais facil de esquecer."""
    assert "populacao_novas_obras" in COLUNAS_QUE_SOMAM
    assert "popN" in campos.NAO_MODELADOS
    fora = agregar([cts(populacao_novas_obras=10.0), cts(populacao_novas_obras=5.0)])
    assert fora["populacao_novas_obras"] == pytest.approx(15.0)


def test_a_cidade_e_herdada_dos_membros():
    fora = agregar([cts(ligacoes_atuais=1), cts(ligacoes_atuais=2)])
    assert fora["cidade_id"] == "c1"


def test_a_macrorregiao_pode_cruzar_municipio():
    """Decidido em 10/09/2026. As medidas somam atravessando a divisa."""
    fora = agregar([
        cts(cidade_id="c1", ligacoes_atuais=100, receita_arrecadada_media_mensal=900.0),
        cts(cidade_id="c2", ligacoes_atuais=40, receita_arrecadada_media_mensal=300.0),
    ])
    assert fora["ligacoes_atuais"] == 140
    assert fora["receita_arrecadada_media_mensal"] == pytest.approx(1200.0)


def test_a_cidade_e_a_de_mais_ligacoes():
    """A ficha expoe UMA cidade, entao escolher e obrigatorio — e a escolha tem de
    significar alguma coisa que se possa conferir na base."""
    fora = agregar([
        cts(cidade_id="pequena", ligacoes_atuais=10),
        cts(cidade_id="grande", ligacoes_atuais=200),
    ])
    assert fora["cidade_id"] == "grande"


def test_varios_membros_da_mesma_cidade_somam_para_a_escolha():
    """Duas CTS pequenas na mesma cidade podem vencer uma grande sozinha."""
    fora = agregar([
        cts(cidade_id="a", ligacoes_atuais=60),
        cts(cidade_id="a", ligacoes_atuais=60),
        cts(cidade_id="b", ligacoes_atuais=100),
    ])
    assert fora["cidade_id"] == "a"


def test_empate_de_cidade_nao_depende_da_ordem():
    """Duas leituras da mesma base tem de dar a mesma ficha."""
    a = agregar([cts(cidade_id="c2", ligacoes_atuais=50), cts(cidade_id="c1", ligacoes_atuais=50)])
    b = agregar([cts(cidade_id="c1", ligacoes_atuais=50), cts(cidade_id="c2", ligacoes_atuais=50)])
    assert a["cidade_id"] == b["cidade_id"] == "c1"


def test_macrorregiao_sem_membro_nao_existe():
    with pytest.raises(ValueError):
        agregar([])


def test_uma_cts_sozinha_agrega_para_ela_mesma():
    """O caso mais comum: 154 dos 186 grupos tem uma CTS so."""
    fora = agregar([cts(ligacoes_atuais=42, receita_arrecadada_media_mensal=999.0)])
    assert fora["ligacoes_atuais"] == 42
    assert fora["receita_arrecadada_media_mensal"] == pytest.approx(999.0)


def test_as_doze_do_databricks_estao_cobertas():
    """A regua daqui e EXATAMENTE a de `campos.DO_DATABRICKS`, traduzida.

    Conferir so a CARDINALIDADE nao morde: trocar uma coluna do Databricks por
    outra qualquer manteria a contagem em 12 e o teste passaria verde. Aqui a
    comparacao e de CONJUNTO, e o de/para (`campos.COLETA`) e quem traduz o nome
    curto para o nome da coluna — sem uma segunda lista escrita a mao para
    envelhecer em ritmo proprio.
    """
    curto_para_coluna = {curto: col for col, curto in campos.COLETA.items()}
    esperado = {curto_para_coluna[c] for c in campos.DO_DATABRICKS}
    assert set(COLUNAS_QUE_SOMAM) - {"populacao_novas_obras"} == esperado


def test_nenhuma_coluna_do_databricks_e_tratada_como_preenchimento():
    """O erro simetrico do teste acima: uma medida cair em `COLUNAS_DA_REGIONAL`.

    Se acontecesse, a macrorregiao pediria que alguem digitasse um numero que a
    base ja mediu — e o valor digitado sobreporia a medida sem gerar trilha.
    """
    curto_para_coluna = {curto: col for col, curto in campos.COLETA.items()}
    do_databricks = {curto_para_coluna[c] for c in campos.DO_DATABRICKS}
    assert not (set(COLUNAS_DA_REGIONAL) & do_databricks)


def test_o_que_a_regional_preenche_e_exatamente_campos_params():
    curto_para_coluna = {curto: col for col, curto in campos.COLETA.items()}
    assert set(COLUNAS_DA_REGIONAL) == {curto_para_coluna[c] for c in campos.CAMPOS_PARAMS}


# ===========================================================================
#  O agrupamento — quais CTS formam cada macrorregiao
# ===========================================================================
from app.dominio.macrorregiao_cts import agrupar  # noqa: E402


def cts_em(macro, empresa="e1", **kw):
    return {"cts": kw.pop("cts", "x"), "sistema_cts": macro, "emp_codigo": empresa, **kw}


def test_agrupa_pelo_par_macrorregiao_e_empresa():
    grupos = agrupar([
        cts_em("m1", cts="cts_1"), cts_em("m1", cts="cts_2"), cts_em("m2", cts="cts_3"),
    ])
    assert set(grupos) == {("m1", "e1"), ("m2", "e1")}
    assert [c["cts"] for c in grupos[("m1", "e1")]] == ["cts_1", "cts_2"]


def test_a_mesma_macrorregiao_em_duas_empresas_nao_funde():
    """Fundir criaria um coletor que nenhuma das duas empresas opera sozinha."""
    grupos = agrupar([cts_em("m1", "e1", cts="a"), cts_em("m1", "e2", cts="b")])
    assert len(grupos) == 2


def test_cts_sem_macrorregiao_fica_de_fora():
    """Ela nao pertence a macrorregiao nenhuma — devolve-la como grupo de um faria
    a tela oferece-la como se fosse uma."""
    grupos = agrupar([cts_em(None, cts="solta"), cts_em("", cts="vazia"), cts_em("m1", cts="ok")])
    assert set(grupos) == {("m1", "e1")}


def test_cts_sem_empresa_tambem_fica_de_fora():
    assert agrupar([cts_em("m1", empresa=None, cts="sem_emp")]) == {}


def test_espaco_em_branco_nao_cria_grupo_fantasma():
    assert agrupar([cts_em("   ", cts="a")]) == {}


def test_agrupar_e_agregar_se_encaixam():
    """O caminho inteiro: agrupa, e cada grupo vira uma ficha somada."""
    ctss = [
        cts_em("m1", cts="a", cidade_id="c1", ligacoes_atuais=40, receita_arrecadada_media_mensal=1000.0),
        cts_em("m1", cts="b", cidade_id="c1", ligacoes_atuais=60, receita_arrecadada_media_mensal=1500.0),
    ]
    (grupo,) = agrupar(ctss).values()
    fora = agregar(grupo)
    assert fora["ligacoes_atuais"] == 100
    assert fora["receita_arrecadada_media_mensal"] == pytest.approx(2500.0)


# --------------------------------------------------------------------------
# QUAIS MACRORREGIÕES A TELA OFERECE
#
# A pergunta mudou quando a macrorregião passou a ganhar linha ao ser colocada
# num sistema (migração 021): antes ela não tinha onde estar colocada, e "nenhum
# membro em sistema" respondia sozinha. Agora são dois lados, e cada teste aqui
# derruba um deles.
# --------------------------------------------------------------------------


def _membro(cts, *, colocada=False, cidade="c1", ligacoes=10, macro="MACRO_A",
            empresa="e1"):
    return {
        "cts": cts,
        "sistema_cts": macro,
        "emp_codigo": empresa,
        "cidade_id": cidade,
        "ligacoes_atuais": ligacoes,
        "colocada": colocada,
    }


def test_macrorregiao_com_todos_os_membros_soltos_e_oferecida():
    grupos = agrupar([_membro("cts_1"), _membro("cts_2")])
    assert livres(grupos, set()) == [{"id": "MACRO_A", "cidId": "c1"}]


def test_membro_ja_colocado_tira_a_macrorregiao_da_lista():
    """Colocá-la seria colocar um coletor que já está noutro sistema."""
    grupos = agrupar(
        [_membro("cts_1", colocada=True), _membro("cts_2")]
    )
    assert livres(grupos, set()) == []


def test_macrorregiao_ja_colocada_sai_da_lista_com_os_membros_soltos():
    """O lado que não existia antes da linha própria.

    Colocada, a macrorregião tem linha em `cts_operacional`, e os MEMBROS
    continuam sem sistema — é assim que se espera que fiquem. Uma regra que
    olhasse só para eles continuaria oferecendo uma macrorregião já montada, e a
    segunda colocação a mudaria de sistema sem ninguém ter pedido.
    """
    grupos = agrupar([_membro("cts_1"), _membro("cts_2")])
    assert livres(grupos, {"MACRO_A"}) == []


def test_uma_colocada_nao_esconde_a_outra():
    grupos = agrupar(
        [_membro("cts_1", macro="MACRO_A"), _membro("cts_9", macro="MACRO_B")]
    )
    assert [m["id"] for m in livres(grupos, {"MACRO_A"})] == ["MACRO_B"]


def test_a_cidade_oferecida_e_a_dominante_e_nao_a_primeira():
    """`cidId` recorta a tela, e tem de ser a mesma resposta em qualquer ordem.

    `c9` tem mais ligações e vem por último; uma implementação que pegasse a
    primeira linha — ou o menor id — devolveria `c1`.
    """
    grupos = agrupar(
        [
            _membro("cts_1", cidade="c1", ligacoes=10),
            _membro("cts_2", cidade="c9", ligacoes=90),
        ]
    )
    assert livres(grupos, set()) == [{"id": "MACRO_A", "cidId": "c9"}]


def test_a_lista_sai_ordenada_pelo_id():
    grupos = agrupar(
        [_membro("cts_1", macro="MACRO_Z"), _membro("cts_2", macro="MACRO_A")]
    )
    assert [m["id"] for m in livres(grupos, set())] == [
        "MACRO_A",
        "MACRO_Z",
    ]


# --------------------------------------------------------------------------
# O NOME QUE DUAS EMPRESAS USAM
#
# A chave da macrorregião é o par `(sistema_cts, emp_codigo)`, e o cadastro
# guarda UM id — `cts_operacional.cts` é chave primária. Um nome repetido entre
# empresas da mesma unidade é, portanto, irrepresentável, e o modo de falha é o
# pior possível: somar coletores que empresas diferentes operam, em silêncio.
# --------------------------------------------------------------------------


def test_nome_de_uma_empresa_so_nao_e_ambiguo():
    grupos = agrupar([_membro("cts_1"), _membro("cts_2")])
    assert nomes_ambiguos(grupos) == set()


def test_o_mesmo_nome_em_duas_empresas_e_ambiguo():
    grupos = agrupar(
        [_membro("cts_1", empresa="e1"), _membro("cts_9", empresa="e2")]
    )
    assert nomes_ambiguos(grupos) == {"MACRO_A"}


def test_macrorregiao_ambigua_nao_e_oferecida():
    """Oferecê-la devolveria um id que não diz qual das duas foi escolhida."""
    grupos = agrupar(
        [_membro("cts_1", empresa="e1"), _membro("cts_9", empresa="e2")]
    )
    assert livres(grupos, set()) == []


def test_a_ambiguidade_de_um_nome_nao_esconde_os_outros():
    grupos = agrupar(
        [
            _membro("cts_1", macro="MACRO_A", empresa="e1"),
            _membro("cts_9", macro="MACRO_A", empresa="e2"),
            _membro("cts_5", macro="MACRO_B", empresa="e1"),
        ]
    )
    assert [m["id"] for m in livres(grupos, set())] == ["MACRO_B"]


def test_o_ambiguo_nao_e_fundido_num_grupo_so():
    """`agrupar` guarda os dois separados — é a fusão que a regra impede.

    Se a chave fosse só o nome, os dois coletores cairiam no mesmo grupo e
    `agregar` somaria as ligações das duas empresas numa ficha só.
    """
    grupos = agrupar(
        [
            _membro("cts_1", empresa="e1", ligacoes=100),
            _membro("cts_9", empresa="e2", ligacoes=7),
        ]
    )
    assert sorted(grupos) == [("MACRO_A", "e1"), ("MACRO_A", "e2")]
    assert [agregar(g)["ligacoes_atuais"] for _k, g in sorted(grupos.items())] == [100, 7]


# --------------------------------------------------------------------------
# O ALARME DE DIVERGÊNCIA
#
# Colocada, a ficha não é recalculada na leitura — é ela que a Regional preenche
# e o motor lê. O preço é ficar para trás numa recarga do Databricks, e estes
# testes prendem o que o alarme promete: acusar o que a ação recomendada
# (gravar a ficha de novo) consegue consertar, e só isso.
# --------------------------------------------------------------------------


def _guardada(**kw):
    """A ficha como está no banco: as somas de quando ela foi colocada.

    O que não se informa fica NULO, e não zero — `divergencias` distingue os dois
    (ADR 0002), e um fixture que zerasse tudo compararia nulo contra zero em onze
    colunas e acusaria divergência em toda leitura.
    """
    base = {c: None for c in COLUNAS_QUE_SOMAM}
    return {**base, **kw}


def test_ficha_igual_a_soma_de_hoje_nao_diverge():
    membros = [cts(ligacoes_atuais=10), cts(ligacoes_atuais=5)]
    assert divergencias(_guardada(ligacoes_atuais=15), membros) == {}


def test_membro_que_mudou_aparece_nomeando_a_coluna():
    membros = [cts(ligacoes_atuais=10), cts(ligacoes_atuais=5)]
    fora = divergencias(_guardada(ligacoes_atuais=99), membros)
    assert list(fora) == ["ligacoes_atuais"]
    assert fora["ligacoes_atuais"] == (99, 15)


def test_a_folga_de_um_centavo_engole_ruido_de_ponto_flutuante():
    """Somar `double precision` em ordens diferentes difere na última casa.

    Um alarme que dispara por isso é um alarme que ensina a ignorar alarmes.
    """
    membros = [cts(receita_faturada_media_mensal=0.1) for _ in range(3)]
    assert divergencias(_guardada(receita_faturada_media_mensal=0.3), membros) == {}


def test_um_centavo_e_meio_de_diferenca_ja_aparece():
    membros = [cts(receita_faturada_media_mensal=100.0)]
    assert "receita_faturada_media_mensal" in divergencias(
        _guardada(receita_faturada_media_mensal=100.015), membros
    )


def test_populacao_de_novas_obras_nao_entra_no_alarme():
    """A única coluna somada que a ESCRITA nunca toca (`NAO_MODELADOS`).

    Acusá-la produziria um aviso que "grave a ficha de novo" não apaga — e um
    alarme que não apaga ensina a ignorar os outros. Ela continua sendo somada
    quando a macrorregião NASCE: nascer certa é diferente de prometer manter.
    """
    assert "populacao_novas_obras" in COLUNAS_QUE_SOMAM
    membros = [cts(populacao_novas_obras=1000)]
    assert divergencias(_guardada(populacao_novas_obras=1), membros) == {}


def test_o_alarme_cobre_todo_o_resto_do_que_se_soma():
    """Coluna nova em `COLUNAS_QUE_SOMAM` entra no alarme sem ninguém lembrar."""
    assert set(COLUNAS_COMPARAVEIS) == set(COLUNAS_QUE_SOMAM) - {
        "populacao_novas_obras"
    }
