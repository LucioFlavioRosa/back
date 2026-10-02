"""OS CINCO NOMES DO CADASTRO PASSARAM A SER GRAVAVEIS — e vazio continua recusado.

Decisao do dono do produto em 01/10/2026. Ele mudou o nome da empresa na planilha do
cadastro, subiu, e leu um aviso dizendo que a coluna nao volta e que para mudar era "na
tela" — onde tambem nao dava, porque nenhuma rota levava nome. A resposta dele: *"isso
esta errado, se mudar pela planilha deve atualizar"*; e depois, sobre os outros quatro:
*"faca todos"*.

## Onde cada nome mora

    empresa          input.empresa.empresa                    PUT /empresas/{cod}
    cidade           input.cidade.cidade_name                 PUT /contrato/{cidade}
    sub-bacia        sistema_topologia.componente_sistema_nome  PUT /sub-bacias/{id}
    ETE              sistema_topologia.componente_sistema_nome  PUT /etes/{id}
    coletor (CTS)    sistema_topologia.componente_sistema_nome  PUT /cts/{id}

Os tres ultimos sao a MESMA coluna: o id deixou de ser o nome (os ids sao slug, e o
coletor tem codigo da origem), e `subbacia_operacional.sub_bacia` / `cts_operacional.cts`
sao as chaves, nao nomes. Por isso ha um gravador so, `_gravar_nome_do_componente`.

## O que estes testes cobrem, e o que nao

Cobrem a REGRA (nome vazio, espacos, numero como nome) e a ESTRUTURA (o mapa da ficha
de empresa, o nome da coluna da topologia) — tudo sem banco. O caminho completo (`PUT` ->
`UPDATE` -> trilha) foi exercitado contra a base de desenvolvimento, porque e a unica
forma de provar que o `UPDATE` acerta a linha certa.

`ete_name` merece nota: a tela SEMPRE deixou digita-lo (`origem: 'un'` no schema do
front) e o `PUT` o descartava calado, porque o mapa `ETE` nao tem campo de nome. Era
perda silenciosa anterior a esta mudanca, da mesma familia que o relato do tester em
30/09/2026.
"""

import inspect

import pytest

from app.dominio.erros import ValorInvalido
from app.infra.repositorios import cadastro_escrita as esc


# --------------------------------------------------------------- a regra do nome
def test_o_nome_viaja_sem_espaco_nas_pontas():
    assert esc.nome_de_ficha("  Empresa 1 Interior10  ", campo="empresa.nome") == "Empresa 1 Interior10"


@pytest.mark.parametrize("vazio", ["", "   ", None])
@pytest.mark.parametrize(
    "campo", ["empresa.nome", "cidade.nome", "sub-bacia.nome", "ete.nome", "cts.nome"]
)
def test_nome_vazio_e_recusado_em_vez_de_apagar(vazio, campo):
    """Apagar numero e correcao; apagar nome deixa a linha inidentificavel.

    A ficha aparece em varias abas e no desenho do fluxo. Aceitar o branco honraria a
    regra "celula vazia apaga o valor", e apagaria dado de verdade por causa de uma
    celula limpa sem intencao — por isso aqui a regra para.

    E A MENSAGEM NOMEIA O CAMPO: com cinco chamadores, "nome nao pode ficar vazio" nao
    diria qual ficha recusou.
    """
    with pytest.raises(ValorInvalido) as erro:
        esc.nome_de_ficha(vazio, campo=campo)
    assert campo in str(erro.value)


def test_um_numero_como_nome_continua_texto():
    """Cidade chamada "1001" e nome, nao numero — nao passa por `numerico`."""
    assert esc.nome_de_ficha(1001, campo="cidade.nome") == "1001"


# --------------------------------------------------------------- a estrutura
def test_a_ficha_de_empresa_grava_o_nome_e_o_fim():
    """O mapa e o contrato: o diff, o `UPDATE` e a trilha saem todos dele.

    Se `nome` sair daqui, o campo deixa de ser gravado SEM ERRO NENHUM — o corpo
    continua sendo aceito e a mudanca some. E o modo de falhar que este teste cobre.
    """
    assert esc._EMPRESA == {"nome": "empresa", "fim": "data_fim_concessao"}


def test_o_nome_do_componente_mora_na_topologia():
    assert esc._NOME_NA_TOPOLOGIA == "componente_sistema_nome"


def test_sem_linha_na_topologia_o_gravador_nao_inventa_topologia():
    """Coletor que a carga nao trouxe como componente nao tem nome exibido.

    Criar a linha para guardar um nome inventaria topologia — e topologia e decisao da
    Regional, tomada no desenho do fluxo. O gravador devolve lista vazia e nao escreve.
    """
    fonte = inspect.getsource(esc._gravar_nome_do_componente)
    assert "if linha is None:" in fonte
    assert "return []" in fonte
    assert "INSERT" not in fonte.upper(), "o gravador do nome nao cria linha de topologia"


@pytest.mark.parametrize(
    "funcao,campo",
    [
        (esc.salvar_coleta, "nome"),
        (esc.salvar_ete, "nome"),
        (esc.salvar_contrato, "nome"),
    ],
)
def test_cada_ficha_so_mexe_no_nome_quando_o_corpo_o_traz(funcao, campo):
    """`if "nome" in corpo`, e nao `corpo.get("nome")`.

    A diferenca morde num `PUT` que nao mande o nome: com `get`, ele viria `None`, o
    `nome_de_ficha` recusaria com 422 e a gravacao de PRECO falharia por causa de um
    campo que o cliente nunca quis mexer. Com `in`, ficha sem a chave nao toca no nome —
    a mesma regua de `obrasOverride` e dos blocos `db`/`params`.
    """
    fonte = inspect.getsource(funcao)
    assert f'"{campo}" in' in fonte


# ------------------------------------------- o tipo da ficha, e as chaves no SQL
def test_cada_rota_exige_que_o_id_seja_do_TIPO_dela():
    """`PUT /sub-bacias/{id_de_coletor}` nao pode passar — e passava.

    `exigir_dona` resolvia a unidade so pela TOPOLOGIA, onde sub-bacia, ETE e coletor
    sao componentes iguais. Bastava o coletor estar na topologia da unidade para a rota
    de sub-bacia aceita-lo, e o upsert de `_gravar_coleta` CRIAVA uma sub-bacia fantasma
    com id de coletor. A ETE ja se protegia com `JOIN ete_capex`, e o comentario de la
    diz por que; as outras duas nao tinham o `JOIN`.

    Achado pela revisao do Codex em 01/10/2026, ao conferir a liberacao do nome — que
    renomeia a linha da topologia por esse mesmo id, e por isso ampliava o alcance.
    """
    assert "subbacia_operacional o ON o.sub_bacia" in esc._DONO["sub-bacia"]
    assert "cts_operacional oc ON oc.cts" in esc._DONO["cts"]
    assert "ete_capex" in esc._DONO["ete"]


def test_o_sql_de_dono_nao_tem_marcador_alem_do_esquema():
    """`.format(i=...)` le TODA chave como marcador, inclusive em comentario SQL.

    Custou um 500: um comentario `-- PUT /cts/{id_de_subbacia}` dentro da string virou
    marcador e estourou `KeyError` na primeira chamada. O texto explicativo vai em
    comentario PYTHON, fora da string, ou sem chaves.
    """
    import re

    for tipo, sql in esc._DONO.items():
        marcadores = set(re.findall(r"\{(\w+)\}", sql))
        assert marcadores <= {"i"}, f"{tipo}: marcador inesperado {marcadores - {'i'}}"


# ------------------------------------------- o modelo nao pode comer o nome
def test_o_modelo_da_ETE_declara_tudo_o_que_a_rota_monta():
    """`response_model` FILTRA: campo nao declarado some da resposta, sem erro.

    Este modo de falha ja morreu duas vezes nesta mesma ficha:

      29/09/2026  `capExpMod`/`capexExpMod` — as duas colunas novas do modulo de
                  expansao eram montadas e descartadas; a tela mostrava vazio e a
                  edicao seguinte gravava vazio por cima do banco.
      01/10/2026  `nome` — liberei a ESCRITA do nome da ETE e a leitura nao o trazia
                  de volta: o banco gravava, a ficha recarregada mostrava o id.

    As duas vezes o sintoma foi o mesmo que o dono do produto relatou de outra forma:
    "mudo e nao vejo diferenca". Por isso o teste compara o mapa de colunas com os
    campos do modelo, em vez de listar nomes a mao — coluna nova entra no mapa e o
    teste cobra o modelo.
    """
    from app.api import formas_cadastro as formas
    from app.infra.repositorios.cadastro import _MAPA_ETE

    declarados = set(formas.Ete.model_fields)
    faltando = set(_MAPA_ETE.values()) - declarados
    assert not faltando, f"o modelo Ete descartaria: {sorted(faltando)}"
    #: e os campos que a rota monta a mao, fora do mapa
    for campo in ("id", "nome", "sub", "cidId", "sisId", "sistema", "nova"):
        assert campo in declarados, f"o modelo Ete descartaria `{campo}`"


# ------------------------------------------- as obras da ficha que acabou de nascer
def test_obra_omitida_no_nascimento_nao_e_recusada():
    """Na ficha que nasceu agora, omitir obra deixa vazio o que ja estava vazio.

    A recusa por componente omitido existe por uma razao boa — a gravacao substitui as
    obras em BLOCO, e a tela nao oferece remover obra, entao omissao nao e intencao. Numa
    ficha recem-criada a premissa nao vale: as cinco obras acabaram de nascer sem numero.

    Exigir as cinco ali obrigaria quem cria pela planilha a reenviar obras em branco so
    para provar que nao quer apaga-las — ou, nao sabendo o vocabulario de cor, a nao poder
    criar nada. Medido contra o banco em 01/10/2026: criar sub-bacia com duas obras
    preenchidas dava 422 e a transacao desfazia a ficha inteira.
    """
    from app.dominio.ficha import obras_da_ficha

    atual = {
        str(i): {"nome": nome, "un": medida}
        for i, (nome, medida) in enumerate(esc._FICHA_NOVA["sub-bacia"][3])
    }
    override = {"1": {"qtd": "2.472", "preco": "1.850"}}

    #: fora do nascimento, recusa — e a mensagem nomeia o que faltou
    with pytest.raises(ValorInvalido) as erro:
        obras_da_ficha(override, atual, esperadas=5, rotulo="sub-bacia")
    assert "seria APAGADO" in str(erro.value)

    #: no nascimento, passa — e as cinco voltam, so uma com numero
    obras = obras_da_ficha(override, atual, esperadas=5, rotulo="sub-bacia", recem_criada=True)
    assert len(obras) == 5
    preenchidas = [o for o in obras if o.get("qtd")]
    assert len(preenchidas) == 1


def test_o_override_aceita_a_chave_por_nome_do_componente():
    """O cliente fala o vocabulario; o servidor resolve o indice.

    O indice SEMPRE saiu do nome (`_INDICE_SUBBACIA`/`_INDICE_CTS`), nunca da ordem das
    linhas — entao aceitar a chave por nome nao inventa nada. Isso e o que permite a uma
    ficha RECEM-CRIADA mandar as obras: ela nunca foi lida, e portanto nao tem os indices.

    Espelhar o mapa no front seria a alternativa, e a pior: vocabulario duplicado
    divergiria, e o sintoma seria numero caindo na obra errada, calado.
    """
    virar = esc._override_por_indice
    assert virar({"Rede coletora": {"qtd": 1}}, "componentes_subbacias_capex") == {"1": {"qtd": 1}}
    #: a forma antiga continua valendo — a tela manda por indice, porque ela leu a ficha
    assert virar({"1": {"qtd": 1}}, "componentes_subbacias_capex") == {"1": {"qtd": 1}}
    #: o vocabulario e POR TABELA: "Tronco" e 1 na CTS e 2 na sub-bacia
    assert virar({"Tronco": {}}, "componentes_cts_capex") == {"1": {}}
    assert virar({"Tronco": {}}, "componentes_subbacias_capex") == {"2": {}}
    #: nome desconhecido passa intacto — quem recusa e `obras_da_ficha`
    assert virar({"Obra Inventada": {}}, "componentes_subbacias_capex") == {"Obra Inventada": {}}


# ------------------------------------------- o sistema nao atravessa unidade
def test_salvar_sistema_confere_a_unidade_do_id_que_ja_existe():
    """`input.sistema` e entidade GLOBAL, e o upsert renomeia por id.

    Sem esta conferencia, a unidade B que reutilizasse um `sistema_id` da unidade A
    renomearia o sistema DELA e o ligaria a uma cidade da B — e as leituras que assumem
    "um sistema, uma unidade" (`_unidade_do_sistema`) passariam a mentir.

    A cidade do corpo ser da unidade NAO bastava: ela nao diz nada sobre o id que se esta
    reutilizando. Achado pela revisao final do Codex em 01/10/2026, no mesmo dia em que
    esta rota nasceu.

    Teste de ESTRUTURA, sem banco: o que se prende e que a conferencia existe e vem ANTES
    da escrita. O caminho completo foi exercitado contra a base de desenvolvimento.
    """
    fonte = inspect.getsource(esc.salvar_sistema)
    assert "_unidade_do_sistema" in fonte, "a unidade do id existente nao e conferida"
    assert "FichaDeOutraUnidade" in fonte
    #: antes do upsert — recusar depois de escrever nao recusa nada
    assert fonte.index("_unidade_do_sistema") < fonte.index("INSERT INTO")


# ------------------------------------------- um sistema, uma unidade
def test_unidade_do_sistema_recusa_quando_o_dado_diz_duas():
    """Decidir POSSE por sorte e pior que recusar.

    A funcao lia so a primeira linha de um `SELECT DISTINCT`, e com isso escolhia a dona
    ao acaso quando o mesmo `sistema_id` estava ligado a cidades de unidades diferentes.
    Quatro chamadores decidem posse com esta resposta.

    UM SISTEMA, UMA UNIDADE e invariante MEDIDA: zero sistemas atravessam unidade nas duas
    bases (a mockada e a real do portfolio), e zero cidades estao em mais de uma empresa —
    "de fato uma cidade, uma empresa", confirmou o dono do produto em 01/10/2026. Um SES
    atende varias CIDADES e pode atravessar EMPRESA (o Saracuruna atravessa), mas dentro
    da mesma unidade.

    Entao duas unidades para um sistema e dado sujo, e dado sujo para a gravacao com o
    nome das unidades envolvidas, para alguem arrumar na origem.
    """
    import asyncio

    from app.dominio.erros import TopologiaInvalida

    class ConFalso:
        def __init__(self, unidades):
            self._unidades = unidades

        async def fetch(self, _sql, *_args):
            return [{"unidade_id": u} for u in self._unidades]

    async def unidade(unidades):
        return await esc._unidade_do_sistema(ConFalso(unidades), "s01")

    assert asyncio.run(unidade(["uA1"])) == "uA1"
    assert asyncio.run(unidade([])) is None
    with pytest.raises(TopologiaInvalida) as erro:
        asyncio.run(unidade(["uA1", "uB2"]))
    #: a mensagem NOMEIA as unidades — sem isso ninguem sabe onde arrumar
    assert "uA1" in str(erro.value) and "uB2" in str(erro.value)
