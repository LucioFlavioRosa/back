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

import re
from typing import Any

from app.config import config
from app.infra import db
from app.infra.repositorios.cadastro import (
    _COLETA,
    CAMPOS_DB,
    CAMPOS_PARAMS,
    NAO_MODELADOS,
    _ficha_coleta,
)


class FichaIncompleta(ValueError):
    """O corpo nao trouxe a ficha inteira — 422, com os campos que faltaram.

    O contrato e explicito: "o corpo carrega a ficha INTEIRA (idempotente), nao um
    patch", e "`params` viaja sempre inteiro". A implementacao, no entanto, gravava
    so as colunas presentes no corpo — ou seja, um PATCH com nome de PUT.

    Enquanto o front manda tudo, os dois coincidem. A divergencia mordia noutro
    lugar: um cliente que ESQUECESSE um campo teria o valor antigo preservado em
    silencio, e o bug dele ficaria invisivel; e ninguem conseguia raciocinar sobre
    o endpoint pelo contrato, porque o contrato descrevia outra coisa.

    Recusar e melhor que aceitar E ZERAR o que faltou: o segundo tambem honraria o
    contrato, mas apagaria dado de verdade por causa de um bug de cliente. Aqui, o
    pior caso e uma requisicao recusada com a lista do que falta.
    """


class FichaDesatualizada(RuntimeError):
    """Alguem gravou esta ficha depois que voce a leu — 409.

    Sem isto, duas pessoas na mesma ficha eram last-write-wins: a segunda gravacao
    apagava a primeira em silencio, e quem perdeu o trabalho so descobria ao
    recarregar. O front ja tem o fluxo de 409 pronto (oferece recarregar do
    servidor); faltava o servidor ter como perceber.
    """


class ValorInvalido(ValueError):
    """Numero fora do formato pt-BR — 422, e nao 500.

    `_numero` devolvia a string crua quando ela nao casava com o formato, e o
    driver estourava com DataError la no `INSERT` -> 500 generico. `"123abc"` num
    campo de preco e erro do usuario, e a resposta precisa dizer o campo.
    """


class FichaDeOutraUnidade(LookupError):
    """A ficha nao pertence a unidade do caminho — vira 404 no endpoint."""


# `1.234,5` -> 1234.5. O contrato e explicito: numero viaja como STRING pt-BR
# estrita, sem unidade nem simbolo. Mandar a string crua para uma coluna
# `double precision` faz o driver recusar; pior, `"2.497,70"` lido como ingles
# viraria 2.49770 — tres ordens de grandeza, sem erro nenhum pelo caminho.
_PT_BR = re.compile(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$|^-?\d+(,\d+)?$")


def _numero(v: Any, campo: str = "") -> Any:
    """String pt-BR vira float; o que ja e numero passa; o que nao e numero segue texto.

    String vazia vira None: no contrato, campo em branco e ausencia — e `wacc`
    vazio significa "usa o WACC medio da unidade". Zero seria outra coisa.
    """
    if v is None or isinstance(v, (int, float, bool)):
        return v
    s = str(v).strip()
    if not s:
        return None
    if not _PT_BR.match(s):
        return v
    return float(s.replace(".", "").replace(",", "."))


#: Campos que NAO podem ser negativos. Vazao, preco, quantidade, receita e
#: populacao sao grandezas fisicas ou monetarias sem sentido abaixo de zero — e
#: uma vazao negativa nao para no cadastro: ela entra na simulacao e sai como um
#: plano que ninguem sabe que esta errado. `wacc` fica de fora de proposito
#: (taxa negativa e exotica mas existe), e `pot` (potencial de crescimento)
#: tambem, porque encolher e um cenario legitimo.
_NAO_NEGATIVOS = {
    "preco", "vaz", "vazInd", "tarr", "ramp",
    "popU", "popA", "fat", "arr", "fatInd", "arrInd",
    "ligU", "ligA", "ligN", "ligUInd", "ligAInd", "ecoU", "ecoA", "ecoN",
    "obra.qtd", "obra.preco", "obra.opex", "obra.dur", "obra.tPred",
}


def _numerico(v: Any, campo: str) -> Any:
    """Como `_numero`, mas para coluna que SO aceita numero: texto vira 422."""
    n = _numero(v)
    if isinstance(n, str):
        raise ValorInvalido(
            f"O campo {campo!r} precisa ser um número no formato 1.234,5 — recebi {v!r}."
        )
    if n is not None and n < 0 and campo in _NAO_NEGATIVOS:
        raise ValorInvalido(f"O campo {campo!r} não pode ser negativo — recebi {v!r}.")
    return n


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
    """Acrescenta a trilha — APPEND-ONLY. Nada aqui apaga nada.

    A primeira versao apagava a trilha da ficha e regravava o conjunto que veio no
    corpo. Era mais simples e estava errada: uma correcao feita ha um mes voltava
    com `gravado_em` de hoje e `override_id` novo. Auditoria que reescreve a data
    do fato nao e auditoria — e um retrato do presente com cara de historico. Uma
    revisao provou: override datado de 01/07 virou 07/08 depois de um PUT que nem
    o mencionava.

    Em troca, so entra o que MUDOU em relacao a ultima linha daquele campo. Sem
    isso, salvar a mesma ficha dez vezes gravaria dez linhas identicas, e quem
    procurasse "quando isto mudou" acharia dez respostas para um evento so.

    O campo que volta ao valor original simplesmente para de vir no corpo. A trilha
    guarda que a correcao existiu — o que e verdade: ela existiu.
    """
    if not overrides:
        return 0

    atuais = {
        l["campo"]: l["valor_novo"]
        for l in await con.fetch(
            f"""SELECT DISTINCT ON (campo) campo, valor_novo
                  FROM {_i()}.override
                 WHERE tipo = $1 AND ficha_id = $2
                 ORDER BY campo, gravado_em DESC, override_id DESC""",
            tipo,
            ficha_id,
        )
    }

    novas = [
        (
            tipo,
            ficha_id,
            unidade_id,
            o.get("campo"),
            _texto(o.get("valorAntigo")),
            _texto(o.get("valorNovo")),
            # O autor vem SEMPRE do token. Aceita-lo do corpo — como esta funcao
            # fazia, com `o.get("autor") or autor` — deixava qualquer um assinar a
            # correcao de outro, e uma revisao gravou "forjado@corp" para provar.
            # Numa trilha de auditoria isso e o defeito que a anula inteira.
            autor,
        )
        for o in overrides
        if _texto(o.get("valorNovo")) != atuais.get(o.get("campo"))
    ]
    if not novas:
        return 0
    await con.executemany(
        f"""INSERT INTO {_i()}.override
                (tipo, ficha_id, unidade_id, campo, valor_antigo, valor_novo, autor)
            VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        novas,
    )
    return len(novas)


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


#: As obras-base, na ordem dos indices que o front usa como chave do override.
#: Vem de `BASE_OBRAS`/`BASE_OBRAS_CTS` em `src/cadastro/domain/` — o corpo manda
#: SO o que difere delas, por indice, entao sem a base aqui nao ha o que gravar.
#: E copia de outra fonte, e copia envelhece: se a base mudar la e nao aqui, a
#: ficha salva com valores de ontem, sem nenhum sinal. Esta na lista de riscos do
#: README, e o certo e o backend servir a base para a tela, e nao o contrario.
_BASE_SUBBACIA = [
    {"nome": "Ligacao de esgoto", "un": "ligacao", "qtd": "244", "preco": "2.497,70",
     "opex": "2.738", "tPred": "11", "dur": "9", "anoObrig": "0", "proibAte": "0", "wacc": "0,091"},
    {"nome": "Rede coletora", "un": "m", "qtd": "2.472,6", "preco": "449,99",
     "opex": "35.659", "tPred": "4", "dur": "6", "anoObrig": "0", "proibAte": "0", "wacc": "0,091"},
    {"nome": "Coletor tronco", "un": "m", "qtd": "0", "preco": "1.200,00",
     "opex": "0", "tPred": "0", "dur": "0", "anoObrig": "0", "proibAte": "0", "wacc": "0,091"},
    {"nome": "Estacao elevatoria (EEE)", "un": "un", "qtd": "0", "preco": "0",
     "opex": "0", "tPred": "0", "dur": "0", "anoObrig": "0", "proibAte": "0", "wacc": ""},
    {"nome": "Linha de recalque (LR)", "un": "m", "qtd": "0", "preco": "900,00",
     "opex": "0", "tPred": "0", "dur": "15", "anoObrig": "0", "proibAte": "0", "wacc": "0,067"},
]


def _obras_da_ficha(override: Any, base: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aplica o override sobre a base — a mesma conta que `mkObras` faz na tela.

    O corpo chega como `Record<indice, Partial<Obra>>`, e nao como lista: a chave e
    a POSICAO na base, e o valor traz so os campos que diferem. Eu tinha assumido
    lista, e o payload real do front estourava com AttributeError na primeira ficha
    salva — a revisao reproduziu.
    """
    if isinstance(override, list):
        return override  # forma antiga; os smokes locais ainda a usam
    override = override or {}
    return [{**b, **(override.get(str(i)) or {})} for i, b in enumerate(base)]


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
            *[_numero(o.get(k)) for k in _OBRA],
            # `_numerico` e nao `_numero`: o segundo devolvia a string crua quando
            # nao reconhecia o formato, e a multiplicacao estourava
            # `TypeError: can't multiply sequence by non-int` -> 500. Numero torto
            # numa obra e erro de quem chamou, e merece 422 dizendo o campo.
            (_numerico(o.get("qtd"), "obra.qtd") or 0)
            * (_numerico(o.get("preco"), "obra.preco") or 0),
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
    colunas = [
        frente_para_coluna[k]
        for k in juntos
        if k in frente_para_coluna and k not in NAO_MODELADOS
    ]
    if not colunas:
        return
    valores = [_numerico(juntos[_COLETA[c]], _COLETA[c]) for c in colunas]
    marc = ", ".join(f"${i + 2}" for i in range(len(colunas)))
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(colunas))
    await con.execute(
        f"""INSERT INTO {_i()}.{tabela} ({chave}, {", ".join(colunas)})
            VALUES ($1, {marc})
            ON CONFLICT ({chave}) DO UPDATE SET {sets}""",
        ficha_id,
        *valores,
    )


# ---------------------------------------------------------------- pertencimento
#: De que unidade e cada tipo de ficha. O `unidade_id` do CAMINHO nao pode ser
#: acreditado: ele so dizia em nome de quem gravar, e nao QUE ficha podia ser
#: gravada — dava para escrever na sub-bacia de outra unidade so trocando o id da
#: URL, e a trilha ainda registrava a unidade errada como dona.
_DONO = {
    "sub-bacia": """
        SELECT s.unidade_id
          FROM {i}.sistema_topologia t
          JOIN {i}.cidade_sistema cs USING (sistema_id)
          JOIN {i}.superintendencia_cidade c ON c.cidade_id = cs.cidade_id
          JOIN {i}.regional_superintendencia s USING (superintendencia_id)
         WHERE t.componente_sistema_id = $1""",
    "cidade": """
        SELECT s.unidade_id
          FROM {i}.superintendencia_cidade c
          JOIN {i}.regional_superintendencia s USING (superintendencia_id)
         WHERE c.cidade_id = $1""",
    "cts": """
        SELECT s.unidade_id
          FROM {i}.subbacia_cts p
          JOIN {i}.sistema_topologia t ON t.componente_sistema_id = p.sub_bacia
          JOIN {i}.cidade_sistema cs USING (sistema_id)
          JOIN {i}.superintendencia_cidade c ON c.cidade_id = cs.cidade_id
          JOIN {i}.regional_superintendencia s USING (superintendencia_id)
         WHERE p.cts = $1""",
    # A ETE percorre o MESMO caminho da sub-bacia, e nao um caminho proprio: em
    # `sistema_topologia` ela e um componente do sistema como qualquer outro. O que
    # a distingue e o id dela tambem existir em `ete_capex` — e assim que o motor a
    # identifica (`otimizador_capex_v62.py:1111`):
    #
    #     if comp in ete_ids: ete_do_sis[d["sistema_id"]] = comp
    #
    # Eu tinha escrito no README que o vinculo era "por convencao de nome" e que o
    # esquema nao tinha caminho ate a unidade. Estava errado: o caminho sempre
    # existiu, e era o mesmo. O `JOIN` com `ete_capex` no fim so garante que o id
    # pedido e mesmo uma ETE, e nao uma sub-bacia entrando pela rota errada.
    "ete": """
        SELECT s.unidade_id
          FROM {i}.sistema_topologia t
          JOIN {i}.ete_capex e ON e.ete_id = t.componente_sistema_id
          JOIN {i}.cidade_sistema cs USING (sistema_id)
          JOIN {i}.superintendencia_cidade c ON c.cidade_id = cs.cidade_id
          JOIN {i}.regional_superintendencia s USING (superintendencia_id)
         WHERE t.componente_sistema_id = $1""",
}


async def exigir_dona(tipo: str, ficha_id: str, unidade_id: str) -> None:
    """A ficha existe E pertence a esta unidade? Senao, 404.

    404 e nao 403 de proposito: responder "existe, mas nao e sua" ja conta quais
    ids existem noutra unidade. Para quem esta no lugar certo o efeito e o mesmo.

    Cobre os quatro tipos. A ETE entrou depois das outras tres: eu achava que o
    esquema nao tinha caminho dela ate a unidade, e tinha — ela e um componente de
    `sistema_topologia` como a sub-bacia, so que com ficha em `ete_capex`.
    """
    sql = _DONO.get(tipo)
    if sql is None:
        return
    linha = await db.buscar_um(sql.format(i=_i()), ficha_id)
    if linha is None or linha["unidade_id"] != unidade_id:
        raise FichaDeOutraUnidade(f"{tipo} {ficha_id!r} nao pertence a unidade {unidade_id!r}")


def _exigir_ficha_inteira(corpo: dict[str, Any]) -> None:
    """`params` e `db` precisam vir COMPLETOS — e o que faz o PUT ser substituicao.

    Bloco AUSENTE passa: `{"overrides": [...]}` sozinho e uma correcao de trilha
    sem tocar na ficha, e exigir os dois blocos ali seria exigir que o cliente
    reenvie dado que nao esta mudando. Bloco PRESENTE, porem, tem de estar inteiro.
    """
    faltando: list[str] = []
    for bloco, esperados in (("params", CAMPOS_PARAMS), ("db", CAMPOS_DB)):
        if bloco not in corpo:
            continue
        recebidos = set(corpo[bloco] or {})
        faltando += [f"{bloco}.{c}" for c in esperados if c not in recebidos]
    if faltando:
        raise FichaIncompleta(
            "O corpo precisa trazer a ficha inteira. Faltaram: "
            + ", ".join(sorted(faltando))
            + ". Campo vazio deve vir como string vazia, não ausente."
        )


async def _versao_atual(tipo: str, ficha_id: str, unidade_id: str) -> str | None:
    """A versão que a ficha tem AGORA no banco, pela mesma conta que o `GET` usa.

    Reusa as funções de leitura de propósito: se a versão fosse calculada por um
    caminho próprio, os dois lados divergiriam na primeira mudança de payload e o
    409 passaria a disparar sem conflito nenhum.
    """
    from app.infra.repositorios import cadastro

    if tipo == "sub-bacia":
        return (await cadastro.sub_bacias(unidade_id))["subs"].get(ficha_id, {}).get("versao")
    if tipo == "cts":
        return (await cadastro.cts(unidade_id))["ctss"].get(ficha_id, {}).get("versao")
    if tipo == "ete":
        return next(
            (e["versao"] for e in (await cadastro.etes(unidade_id))["etes"] if e["id"] == ficha_id),
            None,
        )
    return next(
        (c["versao"] for c in (await cadastro.contrato(unidade_id))["cidades"] if c["id"] == ficha_id),
        None,
    )


async def _exigir_versao(corpo: dict[str, Any], tipo: str, ficha_id: str, unidade_id: str) -> None:
    """Recusa a gravação se a ficha mudou desde a leitura.

    `versao` AUSENTE no corpo passa, de propósito: é um cliente que ainda não
    manda o campo (ou um script de operação), e recusá-lo transformaria uma
    melhoria de segurança numa quebra de compatibilidade. Quem manda, é protegido.
    """
    enviada = corpo.get("versao")
    if not enviada:
        return
    atual = await _versao_atual(tipo, ficha_id, unidade_id)
    if atual and atual != enviada:
        raise FichaDesatualizada(
            "Esta ficha foi alterada por outra pessoa depois que você a abriu. "
            "Recarregue do servidor para ver a versão atual antes de salvar."
        )


# ------------------------------------------------------------------ as fichas
async def salvar_coleta(
    *, unidade_id: str, ficha_id: str, corpo: dict[str, Any], autor: str, e_cts: bool
) -> dict[str, Any]:
    """PUT de sub-bacia ou de CTS — são a mesma ficha em duas tabelas."""
    tabela = "cts_operacional" if e_cts else "subbacia_operacional"
    chave = "cts" if e_cts else "sub_bacia"
    tab_obra = "componentes_cts_capex" if e_cts else "componentes_subbacias_capex"
    tipo = "cts" if e_cts else "sub-bacia"
    await exigir_dona(tipo, ficha_id, unidade_id)

    async with db.transacao() as con:
        # Lock ANTES de conferir a versao. Sem ele, duas requisicoes leem a mesma
        # versao, as duas concordam, e as duas gravam — o conflito passaria batido
        # justamente no caso em que ele existe. E o mesmo padrao do POST /runs.
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", ficha_id)
        _exigir_ficha_inteira(corpo)
        await _exigir_versao(corpo, tipo, ficha_id, unidade_id)
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
                obras=_obras_da_ficha(
                    corpo.get("obrasOverride"), _BASE_SUBBACIA
                ),
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
    await exigir_dona("cidade", cidade_id, unidade_id)
    async with db.transacao() as con:
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", cidade_id)
        await _exigir_versao(corpo, "cidade", cidade_id, unidade_id)
        await con.execute(
            f"""INSERT INTO {_i()}.cidade_operacional
                    (cidade_id, data_fim_concessao, unidade_cobertura)
                VALUES ($1, $2, $3)
                ON CONFLICT (cidade_id) DO UPDATE
                  SET data_fim_concessao = EXCLUDED.data_fim_concessao,
                      unidade_cobertura  = EXCLUDED.unidade_cobertura""",
            cidade_id,
            _numerico(cidade.get("fim"), "cidade.fim"),
            cidade.get("cob"),
        )
        if "metas" in corpo:
            await con.execute(
                f"DELETE FROM {_i()}.metas_cobertura WHERE cidade_id = $1", cidade_id
            )
            await con.executemany(
                f"""INSERT INTO {_i()}.metas_cobertura (cidade_id, ano, cobertura_pct)
                    VALUES ($1, $2, $3)""",
                [
                    (cidade_id, _numerico(m.get("ano"), "meta.ano"),
                     _numerico(m.get("pct"), "meta.pct"))
                    for m in corpo.get("metas") or []
                ],
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
                    (
                        cidade_id,
                        cidade.get("nome"),
                        _numerico(f.get("cob"), "fator.cob"),
                        _numerico(f.get("par"), "fator.par"),
                    )
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


#: Nomes do tipo `Ete` do front -> colunas. Tem de casar com o que `cadastro.etes`
#: devolve, senao a ficha lida nao pode ser salva de volta.
_ETE = {
    "capMod": "capacidade_por_modulo",
    "capexMod": "capex_por_modulo",
    "opexMod": "opex_por_modulo",
    "tExec": "tempo_de_execucao",
    "capNom": "capacidade_nominal_atual",
    "vazOp": "vazao_de_operacao_atual",
    "nova": "nova",
    "terreno": "capex_terreno",
    "modulos": "modulos",
    "wacc": "wacc",
}
#: Colunas de ETE que sao numero — as demais (`nova`) sao texto.
_ETE_NUM = {"capMod","capexMod","opexMod","tExec","capNom","vazOp","terreno","modulos","wacc"}


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
    await exigir_dona("ete", ete_id, unidade_id)
    ete = dict(corpo.get("ete") or {})
    if "nova" in ete:
        ete["nova"] = _nova_para_texto(ete["nova"])
    presentes = [k for k in _ETE if k in ete]
    async with db.transacao() as con:
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", ete_id)
        await _exigir_versao(corpo, "ete", ete_id, unidade_id)
        if presentes:
            colunas = [_ETE[k] for k in presentes]
            marc = ", ".join(f"${i + 2}" for i in range(len(colunas)))
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(colunas))
            await con.execute(
                f"""INSERT INTO {_i()}.ete_capex (ete_id, {", ".join(colunas)})
                    VALUES ($1, {marc})
                    ON CONFLICT (ete_id) DO UPDATE SET {sets}""",
                ete_id,
                *[
                    _numerico(ete[k], f"ete.{k}") if k in _ETE_NUM else ete[k]
                    for k in presentes
                ],
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
async def criar_cts(*, unidade_id: str, sub_id: str, cts: dict[str, Any]) -> dict[str, Any]:
    """Cria a CTS e o pareamento 1:1 com a sub-bacia, numa transação.

    O contrato manda devolver a CTS CRIADA, e é essa versão que o front adota — não
    a cópia que ele enviou. Por isso a resposta é uma releitura do banco: se algo
    for normalizado aqui, a tela fica com o valor real e não com o que ela supôs.
    """
    cts_id = cts.get("id")
    # A CTS ainda nao existe, entao quem prova o pertencimento e a SUB-BACIA
    # pareada: e ela que amarra o novo registro a uma unidade.
    await exigir_dona("sub-bacia", sub_id, unidade_id)
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


async def apagar_cts(*, unidade_id: str, cts_id: str) -> bool:
    """A CTS, suas obras e o par saem juntos.

    O par PRIMEIRO: `subbacia_cts.cts` referencia `cts_operacional`, então apagar a
    CTS antes esbarraria na FK. E deixar o par órfão seria pior que o erro — a tela
    mostraria uma sub-bacia pareada com uma CTS que não existe mais.
    """
    await exigir_dona("cts", cts_id, unidade_id)
    async with db.transacao() as con:
        await con.execute(f"DELETE FROM {_i()}.subbacia_cts WHERE cts = $1", cts_id)
        await con.execute(f"DELETE FROM {_i()}.componentes_cts_capex WHERE cts = $1", cts_id)
        r = await con.execute(f"DELETE FROM {_i()}.cts_operacional WHERE cts = $1", cts_id)
    return r != "DELETE 0"
