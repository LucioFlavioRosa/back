"""O MÓDULO DE EXPANSÃO DA ETE NOVA, no cadastro.

Pedido do cliente em 29/09/2026: na ETE nova constrói-se uma quantidade definida de
módulos iniciais e depois expande-se se necessário, e os dois tipos têm vazão e preço
diferentes. `modulos` continua sendo a quantidade inicial; as duas colunas novas
(migração 026) dizem quanto trata e quanto custa CADA módulo de expansão.

Vazias = iguais ao módulo inicial, que é o caso das 639 ETEs do cadastro. É isso que
permite a migração subir sem mexer no número de nenhuma delas.
"""
import io
import pathlib

import pytest

from app.dominio.ficha import ETE, ETE_NUM
from app.dominio.formato import numerico
from app.infra.db import _EXIGIDO
from app.infra.repositorios.cadastro import _COLUNAS_ETE_SQL, _MAPA_ETE
from app.infra.repositorios.nivel_detalhe import (_capex_expansao, _capex_iniciais,
                                                  _capex_terreno)
from app.infra.repositorios.pendencias import _ETE, _ETE_NOVA

COLUNAS = ("capacidade_por_modulo_expansao", "capex_por_modulo_expansao")
CAMPOS = ("capExpMod", "capexExpMod")
MIGRACAO = "026_o_modulo_de_expansao_da_ete.sql"


# ------------------------------------------------------------------ a ficha
@pytest.mark.parametrize("campo,coluna", zip(CAMPOS, COLUNAS, strict=True))
def test_a_ficha_da_ete_grava_as_duas_colunas(campo, coluna):
    assert ETE[campo] == coluna


@pytest.mark.parametrize("campo", CAMPOS)
def test_as_duas_sao_NUMERO(campo):
    """Sem entrar em `ETE_NUM`, `numerico` não roda e o driver recebe a string crua
    numa coluna `double precision` — o `PATCH` estoura em vez de gravar."""
    assert campo in ETE_NUM
    assert numerico("260.000,50", f"ete.{campo}") == 260000.50


def test_vazio_continua_sendo_AUSENCIA_e_zero_continua_sendo_zero():
    """A distinção que a regra do motor usa: coluna vazia significa "igual ao módulo
    inicial", e um zero declarado significa "este módulo não custa". Se o cadastro
    transformasse vazio em 0, toda ETE nova passaria a ter expansão de graça."""
    assert numerico("", "ete.capexExpMod") is None
    assert numerico("0", "ete.capexExpMod") == 0.0


# ------------------------------------------------------- leitura e gravação juntas
#
# O de/para da leitura é DERIVADO do da gravação, e estes testes prendem a derivação:
# era ela, escrita duas vezes, que permitia uma coluna existir num sentido só.
@pytest.mark.parametrize("coluna", COLUNAS)
def test_a_leitura_traz_as_duas(coluna):
    assert coluna in _MAPA_ETE
    assert f"e.{coluna}" in _COLUNAS_ETE_SQL


def test_a_LEITURA_e_a_GRAVACAO_falam_das_MESMAS_colunas():
    """Uma coluna só na leitura volta vazia no `PUT` e apaga o que estava no banco;
    uma coluna só na gravação nunca é lida pela tela. Nenhum dos dois dá erro.

    `nova` é a única exceção, e por ser TEXTO: ela não passa por `pt_br` e a ficha a
    monta à parte.
    """
    assert set(_MAPA_ETE) == set(ETE.values()) - {"nova"}
    assert set(_MAPA_ETE.values()) == set(ETE) - {"nova"}


def test_capacidade_ociosa_continua_fora_das_duas():
    """Campo derivado (nominal − vazão de operação) não volta no PUT, como o `ticket`
    da sub-bacia. Derivar o mapa não pode ter trazido ele de carona."""
    assert "capacidade_ociosa" not in _MAPA_ETE
    assert "capacidade_ociosa" not in ETE.values()


# ------------------------------------------------------------------ pendências
@pytest.mark.parametrize("coluna", COLUNAS)
def test_as_duas_NAO_sao_pendencia(coluna):
    """Pendência é campo que FALTA, e estas podem legitimamente ficar vazias.

    Cobrá-las tornaria as 639 ETEs do cadastro incompletas de um dia para o outro e
    travaria as unidades — a barra de completude mediria uma decisão de produto como se
    fosse buraco de cadastro.
    """
    assert coluna not in _ETE
    assert coluna not in _ETE_NOVA


# ------------------------------------------------------------------ a migração
def test_a_migracao_existe_e_cria_as_duas_colunas():
    sql = io.open(pathlib.Path("migracoes") / MIGRACAO, encoding="utf-8").read()
    for coluna in COLUNAS:
        assert f"ADD COLUMN IF NOT EXISTS {coluna}" in sql.replace("      ", " ").replace(
            "  ", " "), coluna
    assert "COMMENT ON COLUMN" in sql, "a coluna precisa dizer o que é, no próprio banco"


def test_o_readyz_recusa_banco_sem_a_migracao():
    """A ficha da ETE passa a ter os dois campos, e o `PUT` os grava pelo mapa `ETE`:
    num banco sem a coluna, quem preencher o preço do módulo de expansão recebe 500 ao
    salvar — e a perda não é do campo novo, é da edição toda.

    O motor tolera a ausência (coluna vazia é o comportamento de sempre); a TELA não.
    """
    exigidas = {(tabela, coluna, migracao) for _esquema, tabela, coluna, migracao in _EXIGIDO}
    assert ("ete_capex", "capex_por_modulo_expansao", MIGRACAO) in exigidas


# ------------------------------------------------------- a capacidade que a tela soma
def test_a_capacidade_construida_NAO_multiplica_modulos_por_capacidade():
    """`GET /runs/{id}/global` mostra quanto de capacidade de ETE o plano construiu.

    A conta era `SUM(modulos_construidos × capacidade_modulo)`, que supõe todo módulo do
    mesmo tamanho. A ETE nova pode ter módulo de expansão com capacidade própria: num
    pacote de 150 com expansão de 60, a multiplicação dava 300 onde há 210.
    `capacidade_instalada` já é a soma real, publicada pelo motor.

    É um guarda no TEXTO da consulta porque ela não é isolável sem banco. O número em si
    foi conferido contra as 129 rodadas publicadas do banco de desenvolvimento: as duas
    fórmulas dão o mesmo valor em todas (pior diferença: 9e-13, ruído de float), o que é o
    esperado enquanto nenhuma ETE tem módulos de tamanhos diferentes.
    """
    fonte = io.open(pathlib.Path("app") / "infra" / "repositorios" / "nivel_global.py",
                    encoding="utf-8").read()
    assert "modulos_construidos * capacidade_modulo" not in fonte
    assert "SUM(capacidade_instalada - COALESCE(folga_inicial, 0))" in fonte


# ------------------------------------------------- as três parcelas do CAPEX da ETE
#
# Decisão do dono do produto em 29/09/2026: com módulos de dois preços na mesma ETE,
# `quantidade × unitário` para de fechar o CAPEX, e a obra publica as parcelas.
def _linha(**kw):
    base = {"quantidade": None, "preco_unitario": None, "capex": None,
            "capex_terreno": None, "capex_modulos_iniciais": None,
            "capex_modulos_expansao": None}
    return {**base, **kw}


def test_o_terreno_vem_da_COLUNA_quando_a_rodada_a_tem():
    """E não do residual. Com dois preços o residual mistura o terreno com a diferença
    entre eles: terreno 300.000 + módulo de 500.000 + expansão de 260.000 dá CAPEX de
    1.060.000, e `1.060.000 − 2 × 500.000` daria 60.000 de "terreno".

    Com dois preços a linha não tem unitário — é isso que a consulta emite —, e aí as
    duas parcelas de módulo saem e a conta fecha por elas.
    """
    l = _linha(quantidade=2, preco_unitario=None, capex=1060000.0,
               capex_terreno=300000.0, capex_modulos_iniciais=500000.0,
               capex_modulos_expansao=260000.0)
    assert _capex_terreno(l) == 300000.0
    assert _capex_iniciais(l) == 500000.0
    assert _capex_expansao(l) == 260000.0
    assert (_capex_terreno(l) + _capex_iniciais(l) + _capex_expansao(l)
            == pytest.approx(l["capex"])), "a identidade da linha"


def test_COM_UM_PRECO_SO_a_tela_nao_ganha_coluna_nenhuma():
    """O caso de todo cadastro que deixou as colunas de expansão em branco — o fallback
    que o dono do produto confirmou em 30/09/2026: sem os valores novos, o módulo de
    expansão é igual ao de construção e nada muda.

    Ali `quantidade × unitário` JÁ INCLUI os módulos de expansão. Publicar a parcela ao
    lado faria quem soma a linha contar os mesmos módulos duas vezes: 2 × 500.000 de
    unitário + 500.000 de "expansão" daria 1.500.000 num CAPEX de 1.300.000.
    """
    l = _linha(quantidade=2, preco_unitario=500000.0, capex=1300000.0,
               capex_terreno=300000.0, capex_modulos_iniciais=500000.0,
               capex_modulos_expansao=500000.0)
    assert _capex_iniciais(l) is None
    assert _capex_expansao(l) is None
    assert _capex_terreno(l) == 300000.0
    assert (l["quantidade"] * l["preco_unitario"] + _capex_terreno(l)
            == pytest.approx(l["capex"])), "a identidade de sempre continua fechando"


def test_a_ETE_que_NAO_construiu_expansao_tambem_tem_um_preco_so():
    """Ainda que o cadastro declare o segundo preço: se nenhum módulo de expansão entrou
    no plano, a linha tem um preço, e as parcelas de módulo não saem."""
    l = _linha(quantidade=3, preco_unitario=500000.0, capex=1800000.0,
               capex_terreno=300000.0, capex_modulos_iniciais=1500000.0,
               capex_modulos_expansao=0.0)
    assert _capex_iniciais(l) is None and _capex_expansao(l) is None


def test_UMA_PARCELA_BASTA_quando_a_linha_perdeu_o_unitario():
    """Achado pela segunda revisão do Codex, em 30/09/2026.

    Exigir as DUAS parcelas positivas deixava a linha sem leitura nenhuma quando a parcela
    inicial é legitimamente zero — ETE nova com `modulos` em branco, que são 69 no cadastro
    de 09/2026. Sem unitário e sem parcela, a linha mostrava só o terreno: 300.000 num
    CAPEX de 1.080.000, com 780.000 desaparecidos.

    A pergunta certa não é "há dois preços?", e sim "falta leitura para o dinheiro desta
    linha?".
    """
    l = _linha(quantidade=3, preco_unitario=None, capex=1080000.0,
               capex_terreno=300000.0, capex_modulos_iniciais=0.0,
               capex_modulos_expansao=780000.0)
    assert _capex_expansao(l) == 780000.0
    assert _capex_iniciais(l) is None, "parcela zero não vira coluna de zero"
    assert (_capex_terreno(l) + (_capex_iniciais(l) or 0) + _capex_expansao(l)
            == pytest.approx(l["capex"])), "a linha voltou a fechar"


def test_sem_unitario_e_sem_parcela_nenhuma_nao_se_inventa_nada():
    """O outro lado: obra que não tem unitário e não é ETE não ganha parcela de módulo."""
    l = _linha(quantidade=None, preco_unitario=None, capex=10000.0)
    assert _capex_iniciais(l) is None and _capex_expansao(l) is None


def test_o_terreno_cai_no_RESIDUAL_nas_rodadas_publicadas_antes_da_coluna():
    """As 129 do banco de desenvolvimento. Nenhuma tem módulos de dois preços, então ali
    o residual É o terreno — e apagá-lo tiraria a parcela de todas elas."""
    l = _linha(quantidade=2, preco_unitario=500000.0, capex=1300000.0)
    assert _capex_terreno(l) == 300000.0
    assert _capex_expansao(l) is None


def test_a_obra_sem_terreno_continua_sem_coluna():
    """Zero abriria na tela uma coluna que não explica nada. Vale para a parcela que veio
    da coluna e para a que veio do residual."""
    assert _capex_terreno(_linha(quantidade=10, preco_unitario=1000.0, capex=10000.0)) is None
    assert _capex_terreno(_linha(quantidade=10, preco_unitario=1000.0, capex=10000.0,
                                 capex_terreno=0.0)) is None
    assert _capex_expansao(_linha(capex_modulos_expansao=0.0)) is None


def test_sem_quantidade_nem_coluna_nao_se_inventa_parcela():
    assert _capex_terreno(_linha()) is None
    assert _capex_expansao(_linha()) is None


def test_o_UNITARIO_da_linha_agrupada_so_existe_se_for_UM_SO():
    """Era `MAX(preco_unitario)`, com o comentário dizendo que o valor é o mesmo repetido
    em cada módulo — verdade até a ETE nova poder ter dois preços. `MAX` escolheria o
    maior, e a tela mostraria `quantidade × unitário` acima do CAPEX da própria linha.

    Guarda no texto da consulta, que não é isolável sem banco. O número foi conferido
    contra os 342.934 grupos das rodadas publicadas: nenhum tem mais de um preço distinto,
    e nenhum muda de valor.
    """
    fonte = io.open(pathlib.Path("app") / "infra" / "repositorios" / "nivel_detalhe.py",
                    encoding="utf-8").read()
    assert "MAX(o.preco_unitario) AS preco_unitario" not in fonte
    assert "CASE WHEN COUNT(DISTINCT o.preco_unitario) = 1" in fonte
    # e as parcelas somam no grupo, como o CAPEX
    for col in ("capex_terreno", "capex_modulos_iniciais", "capex_modulos_expansao"):
        assert f"SUM(o.{col})" in fonte, col


def test_a_forma_da_resposta_declara_a_parcela_de_expansao():
    """Sem ela no contrato, a tela não tem como fechar a conta quando há dois preços."""
    from app.api.formas_resultado import ObraLinha
    for campo in ("capexTerreno", "capexIniciais", "capexExpansao"):
        assert campo in ObraLinha.model_fields, campo


# ------------------------------------------------- o que a revisão de PRODUÇÃO achou
#
# A mudança vai para a Azure, com acesso só por VPN. Os dois abaixo são sobre o que
# acontece quando os artefatos sobem fora de ordem — e em rede fechada, o que não se vê
# não se conserta.
def test_a_ficha_da_ETE_NAO_PERDE_os_campos_novos_na_resposta():
    """O modelo de resposta FILTRA a ficha, e os dois campos não estavam declarados.

    `cadastro.etes()` os montava e o Pydantic os descartava: a pessoa salvava o preço do
    módulo de expansão, recarregava a ficha, via vazio, e a próxima edição mandava vazio
    por cima do que estava no banco. Perda silenciosa de dado que ninguém pediu para
    apagar.

    O teste anterior desta mudança não pegou porque chamava o repositório direto, sem
    passar pela camada da API. Achado pela revisão do Codex em 30/09/2026.
    """
    from app.api.formas_cadastro import Ete

    ficha = {c: "" for c in Ete.model_fields}
    ficha.update(id="a1e100", cidId="c1", sisId="s1", sistema="Sistema 1", sub="b1",
                 nova="Sim", capExpMod="25", capexExpMod="260.000,5")
    voltou = Ete.model_validate(ficha).model_dump()
    assert voltou["capExpMod"] == "25"
    assert voltou["capexExpMod"] == "260.000,5"


def test_os_campos_da_ficha_e_do_de_para_sao_OS_MESMOS():
    """O guarda geral, e não só para estes dois campos.

    Três listas descrevem a mesma ficha: o mapa da gravação (`ficha.ETE`), o de/para da
    leitura (derivado dele) e o modelo de RESPOSTA. Uma coluna que falte no terceiro
    desaparece da tela sem erro — foi exatamente o que aconteceu.
    """
    from app.api.formas_cadastro import Ete

    declarados = set(Ete.model_fields)
    #: os que não vêm do mapa: identificação, situação na árvore e auditoria.
    fora_do_mapa = {"id", "cidId", "sisId", "sistema", "sub", "nova",
                    "atualizadoEm", "atualizadoPor"}
    assert set(ETE) - fora_do_mapa <= declarados, (
        f"campo gravável que a resposta não devolve: {sorted(set(ETE) - fora_do_mapa - declarados)}")


def test_o_readyz_tambem_exige_a_migracao_do_schema_de_RESULTADO():
    """Sem ela o pod fica PRONTO e a lista de obras responde 500.

    A lista faz `SUM(o.capex_terreno)` e as duas irmãs. `/readyz` só olhava `input` e
    `controle`, então a falta da migração do resultado aparecia como erro numa rota
    qualquer, longe da causa, com o readiness dizendo que estava tudo bem.

    O esquema de resultado NÃO é fixo (vem de `schema_resultado`), e é por isso que estas
    entram numa lista própria em vez de `_EXIGIDO`, que escreve o esquema na linha.
    """
    from app.infra.db import _EXIGIDO_NO_RESULTADO

    assert ("otim_obra", "capex_modulos_expansao",
            "ddl_resultado_migracao_02.sql") in _EXIGIDO_NO_RESULTADO


def test_as_colunas_que_o_servico_LE_do_resultado_estao_no_gate():
    """Se a consulta passar a ler outra coluna nova do resultado, ela precisa entrar no
    gate junto — senão volta a faltar em silêncio."""
    from app.infra.db import _EXIGIDO_NO_RESULTADO

    fonte = io.open(pathlib.Path("app") / "infra" / "repositorios" / "nivel_detalhe.py",
                    encoding="utf-8").read()
    lidas = {c for c in ("capex_terreno", "capex_modulos_iniciais", "capex_modulos_expansao")
             if f"SUM(o.{c})" in fonte}
    assert lidas, "a consulta deveria ler as parcelas"
    no_gate = {c for _t, c, _a in _EXIGIDO_NO_RESULTADO}
    #: TODAS as lidas, e não uma sentinela. A revisão de produção derrubou o argumento de
    #: que "as três entram no mesmo ALTER, conferir uma basta": o readiness existe para
    #: diagnosticar banco divergente, e divergência é o que sobra de DDL aplicado à mão,
    #: de restauração seletiva ou de um `ALTER` que falhou no meio.
    faltando = lidas - no_gate
    assert not faltando, f"coluna lida pela consulta e fora do gate: {sorted(faltando)}"
