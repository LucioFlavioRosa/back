"""A DEDUPE SÓ PODE BARRAR SIMULAÇÃO CUJA ENTRADA É 100% IGUAL.

A garantia que o dono do produto pediu, e a direção que importa é o FALSO POSITIVO:
barrar um pedido cuja entrada mudou devolve ao usuário um resultado calculado sobre outros
dados, sem aviso, e sem saída pela tela — `/reexecutar` recusa rodada publicada com 409.
Barrar de menos só gasta cluster.

Por isso a cobertura aqui é POR EIXO DE DIFERENÇA, e não um teste genérico: cada dimensão
em que a entrada pode diferir ganha um caso, e o caso afirma que a rodada é LIBERADA.

## Os dois eixos, e por que eles têm mecanismos diferentes

1. **Os parâmetros do pedido** entram no `digest` (um SHA do JSON). Qualquer diferença,
   até de um centavo, muda o digest. Testado sem banco.
2. **O cadastro** é comparado por data: `solicitado_em` da rodada contra a última escrita
   em QUALQUER das 17 tabelas que o motor lê. Precisa de banco, e os casos vivem nos
   smokes ao fim do arquivo.

O eixo 2 já esteve cego duas vezes, e as duas foram achadas revisando, não testando:
a carga não carimbava nada (024) e a conta olhava 4 das 17 tabelas (025). É contra a
terceira vez que este arquivo existe.
"""
import os
from pathlib import Path

import pytest

from app.infra.repositorios.controle import digest

FONTE = Path("app/infra/repositorios/controle.py").read_text(encoding="utf-8")

#: Um pedido completo, como `montar_params` monta. As chaves são as 13 que viajam.
PEDIDO = {
    "UNIDADE": "uA1",
    "USUARIO": "lucio.rosa",
    "BASE_RECEITA": "arrecadada",
    "USAR_CTS": True,
    "COBERTURA_SO_RESIDENCIAL": False,
    "CURVA_ADOCAO": "scurve",
    "DATA_INICIO": [1, 2026],
    "FOCO_COBERTURA": 1.0,
    "PENALIDADE_COBERTURA": "meta+cobertura",
    "ANOS_EXTRA_CONCLUSAO": 3,
    "UNIDADE_COBERTURA": "ligacoes",
    "MAX_TIME_S": 600,
    "ORCAMENTO": {2026: 60e6, 2027: 60e6, 2028: 50e6},
}

#: Um valor DIFERENTE para cada parâmetro, um por vez. É a lista que garante cobertura por
#: eixo: se alguém acrescentar um parâmetro ao pedido e não a esta tabela, o teste de
#: completude abaixo acusa.
OUTRO_VALOR = {
    "UNIDADE": "uB2",
    "USUARIO": "outra.pessoa",
    "BASE_RECEITA": "faturada",
    "USAR_CTS": False,
    "COBERTURA_SO_RESIDENCIAL": True,
    "CURVA_ADOCAO": "linear",
    "DATA_INICIO": [7, 2026],
    "FOCO_COBERTURA": 0.5,
    "PENALIDADE_COBERTURA": "meta",
    "ANOS_EXTRA_CONCLUSAO": 5,
    "UNIDADE_COBERTURA": "economias",
    "MAX_TIME_S": 1200,
    "ORCAMENTO": {2026: 60e6, 2027: 60e6, 2028: 50e6 + 0.01},
}


@pytest.mark.parametrize("chave", sorted(PEDIDO))
def test_QUALQUER_parametro_diferente_libera_a_rodada(chave):
    """Um eixo por parâmetro: mudar só ele já faz a simulação rodar de novo."""
    mudado = {**PEDIDO, chave: OUTRO_VALOR[chave]}
    assert digest(mudado) != digest(PEDIDO), f"mudar {chave} não mudou o digest"


def test_a_lista_de_eixos_cobre_todo_parametro_do_pedido():
    """Se um parâmetro novo entrar no pedido, ele precisa entrar aqui também.

    Sem isto, `test_QUALQUER_parametro_diferente...` continuaria verde cobrindo menos do
    que o nome promete — e o parâmetro novo ficaria sem eixo.
    """
    from app.dominio.parametros import CHAVES_ACEITAS

    #: O que um pedido REAL de hoje carrega. `CHAVES_ACEITAS` é maior: ela espelha o que o
    #: JOB aceita, e parte disso a tela não oferece (ou `montar_params` deriva). Os eixos
    #: cobrem o que viaja; os demais ficam listados aqui, com o motivo, para a diferença
    #: ser deliberada e não esquecimento.
    NAO_VIAJAM = {
        "WORKERS",            # paralelismo do solver, o backend não envia
        "ORCAMENTO_TOTAL",    # derivado, só na redistribuição que a tela não oferece
        "HORIZONTE_CAPEX",    # derivado do cronograma de orçamento
        "ETE_FASEADA", "ETE_FIXO",      # o job fixa o modo, a tela não escolhe
        "METAS_COBERTURA", "PESO_COBERTURA", "PESO_CIDADE",   # vêm do cadastro
        "REGIONAL",           # derivada da unidade
    }
    faltam = sorted(k for k in CHAVES_ACEITAS - NAO_VIAJAM if k not in PEDIDO)
    assert not faltam, f"parâmetros sem eixo de teste: {faltam}"


def test_UM_CENTAVO_no_teto_de_um_ano_ja_libera():
    """A pergunta que o dono do produto fez: 1% num CAPEX de um ano basta?

    Basta com folga — o digest é um SHA, e não há tolerância. Um centavo num ano só, num
    cronograma de nove, já é outro pedido.
    """
    um_centavo = {**PEDIDO, "ORCAMENTO": {**PEDIDO["ORCAMENTO"], 2027: 60e6 + 0.01}}
    assert digest(um_centavo) != digest(PEDIDO)


def test_a_mesma_soma_distribuida_de_outro_jeito_libera():
    """Trocar dinheiro entre anos é outro plano, mesmo com o total igual."""
    trocado = {**PEDIDO, "ORCAMENTO": {2026: 50e6, 2027: 60e6, 2028: 60e6}}
    assert sum(trocado["ORCAMENTO"].values()) == sum(PEDIDO["ORCAMENTO"].values())
    assert digest(trocado) != digest(PEDIDO)


def test_o_MESMO_valor_escrito_de_outro_jeito_NAO_libera():
    """O outro lado: não pode rodar de novo por causa de formatação.

    `montar_params` normaliza o cronograma para `{int(ano): float(valor)}`, então
    `"2027": "60000000"` e `2027: 60000000.0` são o mesmo pedido. Sem a normalização, o
    front mandando string e o SDK mandando número gastariam duas execuções do cluster para
    o mesmo plano.
    """
    from app.dominio.parametros import montar_params

    corpo_a = {"unidade_id": "uA1", "orcamento": {"2027": 60000000, "2028": "50000000.00"}}
    corpo_b = {"unidade_id": "uA1", "orcamento": {2027: 60000000.0, 2028: 5e7}}
    a = montar_params(corpo_a, unidade_id="uA1", usuario="lucio.rosa")
    b = montar_params(corpo_b, unidade_id="uA1", usuario="lucio.rosa")
    assert digest(a) == digest(b)


def test_a_ida_e_volta_pelo_JSONB_nao_muda_o_digest():
    """O pedido novo tem chave `int`; o gravado volta do banco como `string`.

    Se isso mudasse o digest, a dedupe NUNCA dispararia para rodada concluída — e o
    sintoma seria "gasta cluster à toa", que ninguém nota. `json.dumps` converte chave
    inteira para string, então os dois coincidem; o teste prende a coincidência.
    """
    gravado = {**PEDIDO, "ORCAMENTO": {str(a): v for a, v in PEDIDO["ORCAMENTO"].items()}}
    assert digest(gravado) == digest(PEDIDO)


def test_A_CONTA_DO_CADASTRO_OLHA_AS_DEZESSETE_TABELAS_QUE_O_MOTOR_LE():
    """O eixo do cadastro, sem precisar de banco: a consulta cita todas elas.

    O motor lê 17 tabelas de `input.*` (`pacote-motor-main/carregar_postgres.py`, `ABAS`).
    Até a migração 025 a conta olhava QUATRO — as fichas —, e mudar as obras, as metas de
    cobertura, as faixas de paridade, o par sub-bacia↔CTS ou o orçamento não movia a data.
    A dedupe barrava um pedido cuja entrada era diferente, que é o defeito que este
    arquivo existe para impedir.
    """
    lidas = {
        "subbacia_operacional", "componentes_subbacias_capex", "subbacia_cts",
        "cidade_operacional", "metas_cobertura", "fator_esgoto",
        "ete_capex", "cts_operacional", "componentes_cts_capex",
        "orcamento", "regional_operacional", "unidade_regional",
        "diretoria", "empresa", "cidade_empresa", "cidade_sistema", "sistema_topologia",
    }
    assert len(lidas) == 17
    faltam = [t for t in sorted(lidas) if f".{t} " not in FONTE and f".{t}\n" not in FONTE]
    assert not faltam, f"tabelas que o motor lê e a dedupe não olha: {faltam}"


# --------------------------------------------------------------------------- smokes
#: Os casos do eixo do cadastro precisam de banco: o que se prova é que uma escrita move a
#: data e a dedupe solta. Sem banco eles pulam, como os outros smokes do projeto.
def _banco_disponivel() -> bool:
    return bool(os.environ.get("POSTGRES_URL", "").endswith("/otimizador"))


TABELAS_E_RECORTE = [
    ("subbacia_operacional", "sub_bacia = $1"),
    ("componentes_subbacias_capex", "sub_bacia = $1"),
    ("metas_cobertura", "cidade_id = (SELECT cidade_id FROM input.subbacia_operacional"
                        " WHERE sub_bacia = $1)"),
    ("fator_esgoto", "cidade_id = (SELECT cidade_id FROM input.subbacia_operacional"
                     " WHERE sub_bacia = $1)"),
    ("orcamento", ""),
    ("sistema_topologia", ""),
]


@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real (POSTGRES_URL de teste)")
@pytest.mark.parametrize("tabela,recorte", TABELAS_E_RECORTE)
@pytest.mark.asyncio
async def test_escrever_numa_tabela_do_motor_libera_a_rodada(tabela, recorte):
    """Um `UPDATE` que não toca na auditoria — a carga — tem de soltar a dedupe.

    Escreve e DESFAZ (savepoint + rollback): o banco de desenvolvimento tem o cadastro de
    trabalho, e um teste não pode deixar marca nele.
    """
    from app.infra import db
    from app.infra.repositorios import controle

    async with db.transacao() as con:
        linha = await con.fetchrow(
            """SELECT r.unidade, r.params FROM controle.run_request r
                 JOIN controle.run_status s USING (run_id)
                WHERE s.status = 'SUCESSO'
                  AND EXISTS (SELECT 1 FROM public.otim_meta m WHERE m.run_id = r.run_id)
                ORDER BY r.solicitado_em DESC LIMIT 1"""
        )
        if not linha:
            pytest.skip("nenhuma rodada publicada no banco")
        params, unidade = dict(linha["params"] or {}), linha["unidade"]
        if not await controle.rodada_identica(con, unidade, params):
            pytest.skip("a rodada mais recente já não é reaproveitável")

        sub = await con.fetchval(
            """WITH sis AS (
                 SELECT DISTINCT s.sistema_id FROM input.sistema s
                   JOIN input.cidade_sistema cs ON cs.sistema_id = s.sistema_id
                   JOIN input.cidade_empresa ce ON ce.cidade_id = cs.cidade_id
                   JOIN input.empresa e ON e.emp_codigo = ce.emp_codigo
                  WHERE e.unidade_id = $1)
               SELECT b.sub_bacia FROM input.subbacia_operacional b
                 JOIN input.sistema_topologia t ON t.componente_sistema_id = b.sub_bacia
                 JOIN sis s USING (sistema_id) LIMIT 1""",
            unidade,
        )
        coluna = await con.fetchval(
            """SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'input' AND table_name = $1
                  AND column_name NOT IN ('carregado_em','atualizado_em','atualizado_por')
                ORDER BY ordinal_position LIMIT 1""",
            tabela,
        )
        sql = f"UPDATE input.{tabela} SET {coluna} = {coluna}"
        if recorte:
            sql += " WHERE " + recorte.replace("$1", f"'{sub}'")

        await con.execute("SAVEPOINT caso")
        try:
            marca = await con.execute(sql)
            if str(marca).endswith(" 0"):
                pytest.skip(f"{tabela} não tem linha no recorte desta unidade")
            assert await controle.rodada_identica(con, unidade, params) is None, (
                f"escrever em {tabela} NÃO liberou a rodada — a dedupe barraria um pedido"
                " cuja entrada mudou"
            )
        finally:
            await con.execute("ROLLBACK TO SAVEPOINT caso")


@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real (POSTGRES_URL de teste)")
@pytest.mark.asyncio
async def test_a_carga_nao_apaga_quem_salvou_pela_tela():
    """`atualizado_em` é da gravação HUMANA, e a carga não pode escrever nele.

    É o que o cabeçalho da ficha mostra ("salvo por X em Y"), e nulo ali quer dizer "nunca
    foi salva pela tela". Foi por isso que a 024 criou coluna nova em vez de carimbar esta.
    """
    from app.infra import db

    async with db.transacao() as con:
        sub = await con.fetchval("SELECT sub_bacia FROM input.subbacia_operacional LIMIT 1")
        antes = await con.fetchrow(
            "SELECT atualizado_em, atualizado_por FROM input.subbacia_operacional"
            " WHERE sub_bacia = $1",
            sub,
        )
        await con.execute("SAVEPOINT caso")
        try:
            await con.execute(
                "UPDATE input.subbacia_operacional SET ligacoes_atuais = ligacoes_atuais"
                " WHERE sub_bacia = $1",
                sub,
            )
            depois = await con.fetchrow(
                "SELECT atualizado_em, atualizado_por, carregado_em"
                "  FROM input.subbacia_operacional WHERE sub_bacia = $1",
                sub,
            )
            assert depois["atualizado_em"] == antes["atualizado_em"]
            assert depois["atualizado_por"] == antes["atualizado_por"]
            assert depois["carregado_em"] is not None, "o trigger não carimbou"
        finally:
            await con.execute("ROLLBACK TO SAVEPOINT caso")
