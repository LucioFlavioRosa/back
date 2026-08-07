"""Escrita do cadastro — a ficha e a trilha, na mesma transação.

Separado da leitura porque as duas têm riscos opostos: ler errado mostra número
errado numa tela; gravar errado apaga o trabalho de alguém. Ficam em arquivos
diferentes para que quem vier mexer saiba em qual dos dois está.

Duas regras atravessam tudo aqui:

  - **uma ficha por vez, e o corpo é a ficha INTEIRA** — não um patch. Isso torna o
    PUT idempotente: reenviar o mesmo corpo não acumula efeito, e uma reconexão no
    meio do salvamento não deixa meia ficha gravada.
  - **ficha e trilha entram na MESMA transação.** Separá-las abriria a janela em que
    o dado já foi corrigido e a auditoria ainda não sabe — e é justamente nessa
    janela que um processo cai.
"""

from typing import Any

from app.config import config
from app.infra import db
from app.infra.repositorios.cadastro import _COLETA, _ficha_coleta


def _i() -> str:
    return config().schema_input


def _texto(v: Any) -> str | None:
    """A trilha guarda TEXTO, e não o tipo original.

    Um override é o registro do que foi digitado, não um valor a recalcular. Texto
    sobrevive a mudança de tipo da coluna, guarda "0,5" como a pessoa escreveu, e
    não obriga a trilha a ter uma coluna por tipo.
    """
    return None if v is None else str(v)


async def _gravar_overrides(
    con: Any,
    *,
    tipo: str,
    ficha_id: str,
    unidade_id: str,
    autor: str,
    overrides: list[dict[str, Any]],
) -> int:
    """Substitui a trilha DESTA ficha pela que veio no corpo.

    Apaga e regrava, e não acrescenta, porque o front manda o conjunto ATUAL de
    divergências — e ele já retira da lista o campo que voltou ao valor original
    ("X virou X" não chega até aqui). Se este código só acrescentasse, desfazer uma
    correção deixaria a trilha afirmando uma divergência que não existe mais, e a
    auditoria passaria a acusar gente por correção que ela mesma desfez.
    """
    await con.execute(
        f"DELETE FROM {_i()}.override WHERE tipo = $1 AND ficha_id = $2", tipo, ficha_id
    )
    if not overrides:
        return 0
    await con.executemany(
        f"""INSERT INTO {_i()}.override
                (tipo, ficha_id, unidade_id, campo, valor_antigo, valor_novo, autor)
            VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        [
            (
                tipo,
                ficha_id,
                unidade_id,
                o.get("campo"),
                _texto(o.get("valorAntigo")),
                _texto(o.get("valorNovo")),
                o.get("autor") or autor,
            )
            for o in overrides
        ],
    )
    return len(overrides)


#: Componentes da ficha de obra -> colunas. `capex` NÃO entra: é calculado
#: (`qtd × preco`) e o contrato diz que não viaja no payload. Recebê-lo seria
#: aceitar uma segunda opinião sobre a mesma conta.
_OBRA = {
    "qtd": "quantidade",
    "un": "unidade",
    "preco": "preco_unitario",
    "opex": "opex",
    "tPred": "tempo_predecessoras",
    "dur": "tempo_execucao",
    "anoObrig": "obra_obrigatoria_ano",
    "proibAte": "obra_proibida_ate",
    "wacc": "wacc",
}


async def _gravar_obras(
    con: Any, *, tabela: str, chave: str, ficha_id: str, obras: list[dict[str, Any]]
) -> None:
    """As obras da ficha, substituídas em bloco.

    `capex` é derivado aqui (`quantidade × preco_unitario`) porque a tela não o
    manda. Calcular no servidor mantém uma conta só: se os dois lados calculassem,
    divergiriam por arredondamento e ninguém saberia qual está no plano.

    `anoObrig` e `proibAte` são CÓDIGOS, não anos quaisquer (`0` = sem restrição,
    `-1` = obrigatória em qualquer ano). Por isso vão como vieram, sem `or 0`:
    tratar ausência como zero afirmaria "sem restrição" onde a resposta é silêncio.
    """
    await con.execute(f"DELETE FROM {_i()}.{tabela} WHERE {chave} = $1", ficha_id)
    if not obras:
        return

    colunas = [chave, "componente", *_OBRA.values(), "capex"]
    marc = ", ".join(f"${i + 1}" for i in range(len(colunas)))
    linhas = [
        (
            ficha_id,
            o.get("nome"),
            *[o.get(k) for k in _OBRA],
            float(o.get("qtd") or 0) * float(o.get("preco") or 0),
        )
        for o in obras
    ]
    await con.executemany(
        f"INSERT INTO {_i()}.{tabela} ({', '.join(colunas)}) VALUES ({marc})", linhas
    )


async def _gravar_coleta(
    con: Any,
    *,
    tabela: str,
    chave: str,
    ficha_id: str,
    params: dict[str, Any],
    bloco_db: dict[str, Any],
) -> None:
    """A ficha de coleta (sub-bacia ou CTS) — os dois blocos na mesma linha.

    `params` viaja sempre inteiro, inclusive `popU`/`popA`. A régua de cobertura da
    cidade decide se esses dois APARECEM na tela e se contam pendência; não se são
    gravados. Trocar a régua de uma cidade não pode apagar o que alguém preencheu.
    """
    juntos = {**bloco_db, **params}
    frente_para_coluna = {v: k for k, v in _COLETA.items()}
    colunas = [frente_para_coluna[k] for k in juntos if k in frente_para_coluna]
    if not colunas:
        return
    valores = [juntos[_COLETA[c]] for c in colunas]
    marc = ", ".join(f"${i + 2}" for i in range(len(colunas)))
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(colunas))
    await con.execute(
        f"""INSERT INTO {_i()}.{tabela} ({chave}, {", ".join(colunas)})
            VALUES ($1, {marc})
            ON CONFLICT ({chave}) DO UPDATE SET {sets}""",
        ficha_id,
        *valores,
    )


# ------------------------------------------------------------------ as fichas
async def salvar_coleta(
    *, unidade_id: str, ficha_id: str, corpo: dict[str, Any], autor: str, e_cts: bool
) -> dict[str, Any]:
    """PUT de sub-bacia ou de CTS — são a mesma ficha em duas tabelas."""
    tabela = "cts_operacional" if e_cts else "subbacia_operacional"
    chave = "cts" if e_cts else "sub_bacia"
    tab_obra = "componentes_cts_capex" if e_cts else "componentes_subbacias_capex"

    async with db.transacao() as con:
        await _gravar_coleta(
            con,
            tabela=tabela,
            chave=chave,
            ficha_id=ficha_id,
            params=corpo.get("params") or {},
            bloco_db=corpo.get("db") or {},
        )
        # `in` e não `or []`: ficha SEM a chave não mexe nas obras; ficha COM a
        # chave e lista vazia apaga todas. São intenções diferentes.
        if "obrasOverride" in corpo:
            await _gravar_obras(
                con,
                tabela=tab_obra,
                chave=chave,
                ficha_id=ficha_id,
                obras=corpo.get("obrasOverride") or [],
            )
        n = await _gravar_overrides(
            con,
            tipo="cts" if e_cts else "sub-bacia",
            ficha_id=ficha_id,
            unidade_id=unidade_id,
            autor=autor,
            overrides=corpo.get("overrides") or [],
        )
    return {"id": ficha_id, "overridesGravados": n}


async def salvar_contrato(
    *, unidade_id: str, cidade_id: str, corpo: dict[str, Any], autor: str
) -> dict[str, Any]:
    """PUT da ficha de cidade: a cidade, suas metas e suas faixas de paridade.

    Metas e faixas são substituídas em bloco, e não mescladas: a tela edita a
    tabela inteira, e mesclar deixaria viva no banco uma linha que o usuário
    apagou na tela — a meta removida continuaria valendo na simulação.
    """
    cidade = corpo.get("cidade") or {}
    async with db.transacao() as con:
        await con.execute(
            f"""INSERT INTO {_i()}.cidade_operacional
                    (cidade_id, data_fim_concessao, unidade_cobertura)
                VALUES ($1, $2, $3)
                ON CONFLICT (cidade_id) DO UPDATE
                  SET data_fim_concessao = EXCLUDED.data_fim_concessao,
                      unidade_cobertura  = EXCLUDED.unidade_cobertura""",
            cidade_id,
            cidade.get("fimConcessao"),
            cidade.get("cob"),
        )
        if "metas" in corpo:
            await con.execute(
                f"DELETE FROM {_i()}.metas_cobertura WHERE cidade_id = $1", cidade_id
            )
            await con.executemany(
                f"""INSERT INTO {_i()}.metas_cobertura (cidade_id, ano, cobertura_pct)
                    VALUES ($1, $2, $3)""",
                [(cidade_id, m.get("ano"), m.get("pct")) for m in corpo.get("metas") or []],
            )
        if "fator" in corpo:
            await con.execute(
                f"DELETE FROM {_i()}.fator_esgoto WHERE cidade_id = $1", cidade_id
            )
            await con.executemany(
                f"""INSERT INTO {_i()}.fator_esgoto
                        (cidade_id, cidade_name, cobertura_pct, paridade)
                    VALUES ($1, $2, $3, $4)""",
                [
                    (cidade_id, cidade.get("nome"), f.get("coberturaPct"), f.get("paridade"))
                    for f in corpo.get("fator") or []
                ],
            )
        n = await _gravar_overrides(
            con,
            tipo="cidade",
            ficha_id=cidade_id,
            unidade_id=unidade_id,
            autor=autor,
            overrides=corpo.get("overrides") or [],
        )
    return {"id": cidade_id, "overridesGravados": n}


_ETE = {
    "capMod": "capacidade_por_modulo",
    "capexMod": "capex_por_modulo",
    "opexMod": "opex_por_modulo",
    "tempoPred": "tempo_predecessoras",
    "tempoExec": "tempo_de_execucao",
    "capAtual": "capacidade_nominal_atual",
    "vazaoAtual": "vazao_de_operacao_atual",
    "ociosa": "capacidade_ociosa",
    "obrigAno": "obra_obrigatoria_ano",
    "proibidaAte": "obra_proibida_ate",
    "nova": "nova",
    "terreno": "capex_terreno",
    "modulos": "modulos",
    "wacc": "wacc",
}


def _nova_para_texto(v: Any) -> Any:
    """`ete_capex.nova` e TEXT no cadastro, e o front manda booleano.

    O motor le assim (`otimizador_capex_v62.py:1222`):

        str(d.get("nova","Nao")).strip().lower() in ("sim","s","true","1")

    Ou seja, ele aceita varias grafias, mas a coluna e texto e um `True` do Python
    estoura no driver antes de chegar la. A traducao mora aqui porque este e o
    unico ponto onde a convencao da tela e a do banco se encontram — e "Sim"/"Nao"
    e o que um humano abrindo a tabela espera ver.
    """
    if isinstance(v, bool):
        return "Sim" if v else "Nao"
    return v


async def salvar_ete(
    *, unidade_id: str, ete_id: str, corpo: dict[str, Any], autor: str
) -> dict[str, Any]:
    ete = dict(corpo.get("ete") or {})
    if "nova" in ete:
        ete["nova"] = _nova_para_texto(ete["nova"])
    presentes = [k for k in _ETE if k in ete]
    async with db.transacao() as con:
        if presentes:
            colunas = [_ETE[k] for k in presentes]
            marc = ", ".join(f"${i + 2}" for i in range(len(colunas)))
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(colunas))
            await con.execute(
                f"""INSERT INTO {_i()}.ete_capex (ete_id, {", ".join(colunas)})
                    VALUES ($1, {marc})
                    ON CONFLICT (ete_id) DO UPDATE SET {sets}""",
                ete_id,
                *[ete[k] for k in presentes],
            )
        n = await _gravar_overrides(
            con,
            tipo="ete",
            ficha_id=ete_id,
            unidade_id=unidade_id,
            autor=autor,
            overrides=corpo.get("overrides") or [],
        )
    return {"id": ete_id, "overridesGravados": n}


# ---------------------------------------------------------------------- CTS
async def criar_cts(*, sub_id: str, cts: dict[str, Any]) -> dict[str, Any]:
    """Cria a CTS e o pareamento 1:1 com a sub-bacia, numa transação.

    O contrato manda devolver a CTS CRIADA, e é essa versão que o front adota — não
    a cópia que ele enviou. Por isso a resposta é uma releitura do banco: se algo
    for normalizado aqui, a tela fica com o valor real e não com o que ela supôs.
    """
    cts_id = cts.get("id")
    async with db.transacao() as con:
        await _gravar_coleta(
            con,
            tabela="cts_operacional",
            chave="cts",
            ficha_id=cts_id,
            params=cts.get("params") or {},
            bloco_db=cts.get("db") or {},
        )
        await con.execute(
            f"""INSERT INTO {_i()}.subbacia_cts (sub_bacia, cts) VALUES ($1, $2)
                ON CONFLICT (sub_bacia) DO UPDATE SET cts = EXCLUDED.cts""",
            sub_id,
            cts_id,
        )
    linha = await db.buscar_um(f"SELECT * FROM {_i()}.cts_operacional WHERE cts = $1", cts_id)
    return {"par": {"sub": sub_id, "cts": cts_id}, "cts": _ficha_coleta(linha, "cts")}


async def apagar_cts(cts_id: str) -> bool:
    """A CTS, suas obras e o par saem juntos.

    O par PRIMEIRO: `subbacia_cts.cts` referencia `cts_operacional`, então apagar a
    CTS antes esbarraria na FK. E deixar o par órfão seria pior que o erro — a tela
    mostraria uma sub-bacia pareada com uma CTS que não existe mais.
    """
    async with db.transacao() as con:
        await con.execute(f"DELETE FROM {_i()}.subbacia_cts WHERE cts = $1", cts_id)
        await con.execute(f"DELETE FROM {_i()}.componentes_cts_capex WHERE cts = $1", cts_id)
        r = await con.execute(f"DELETE FROM {_i()}.cts_operacional WHERE cts = $1", cts_id)
    return r != "DELETE 0"
