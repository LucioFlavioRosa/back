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
from app.dominio import macrorregiao_cts

# A cardinalidade vem de `pendencias`, e nao de um numero repetido aqui: e a MESMA
# regua que o `/prontidao` usa para denunciar obra ausente. Duas copias dela
# fariam a tela dizer que a ficha esta incompleta e o `PUT` aceita-la — ou o
# contrario, que e pior.
from app.dominio.campos import (
    COLETA,
    NAO_MODELADOS,
    OBRAS_CTS,
    OBRAS_DA_CTS,
    OBRAS_SUBBACIA,
)
from app.dominio.erros import FichaDeOutraUnidade, FichaIncompleta, TopologiaInvalida
from app.dominio.ficha import (
    ETE,
    ETE_NUM,
    OBRA,
    capex,
    exigir_ficha_inteira,
    obras_da_ficha,
    valor_de_obra,
)
from app.dominio.formato import numerico, texto_trilha
from app.dominio.topologia import (
    ciclo_ao_ligar,
    id_ou_nada,
    pedido_do_corpo,
    problemas_do_sistema,
)
from app.dominio.trilha import REGIONAL, Alteracao, diferencas, origem_do_campo
from app.infra import db
from app.infra.repositorios.recortes import CIDADES_DA_UNIDADE


def _i() -> str:
    return config().schema_input


async def _registrar(
    con: Any,
    *,
    tipo: str,
    ficha_id: str,
    unidade_id: str,
    autor: str,
    mudancas: list[Alteracao],
) -> int:
    """Acrescenta a trilha — APPEND-ONLY. Nada aqui apaga nada.

    Cada gravação acrescenta só as diferenças que o servidor observou na
    transação atual; as linhas antigas conservam o autor, a data e o id que
    tiveram. Auditoria que reescreve a data do fato não é auditoria.

    **Quem compara é o servidor**, que tem as duas pontas: o que está gravado e o
    que chegou no corpo. O cliente não informa o que mudou — auditoria que
    pergunta ao auditado tem o defeito no desenho, e um cliente com bug apagaria
    o rastro sem sinal.

    Não há deduplicação, e nem é preciso: comparando com o dado gravado, salvar a
    mesma ficha dez vezes não produz diferença nenhuma.
    """
    if not mudancas:
        return 0
    await con.executemany(
        f"""INSERT INTO {_i()}.override
                (tipo, ficha_id, unidade_id, campo, valor_antigo, valor_novo,
                 autor, origem)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        [
            (
                tipo,
                ficha_id,
                unidade_id,
                m.campo,
                m.antes,
                m.depois,
                # O autor vem SEMPRE do token, nunca do corpo: quem pudesse
                # escolher o nome que assina poderia assinar a correcao de outro,
                # e uma trilha assim nao vale nada.
                autor,
                m.origem,
            )
            for m in mudancas
        ],
    )
    return len(mudancas)


async def _obras_gravadas(
    con: Any, tabela: str, chave: str, ficha_id: str
) -> dict[str, dict[str, Any]]:
    """As obras que a ficha JA TEM, na forma `{indice: {campo: valor}}`.

    Mesma forma que o `GET` devolve em `obrasOverride`, para o merge abaixo ser
    campo a campo. E o BANCO — nao um literal — que preenche o que o corpo omitir.

    O `nome` vem junto, e e ele que volta para a coluna `componente` na gravacao.
    Antes vinha da base literal, e a base usava o vocabulario da SUB-BACIA nas
    duas tabelas: regravar uma CTS trocava `Tronco` por `Coletor tronco` e `EEE`
    por `Estacao elevatoria (EEE)`, e o motor deixava de reconhecer o componente
    (`otimizador_capex_v62.py:1136` casa pelo nome). Vindo da linha gravada, cada
    tabela conserva o vocabulario dela sem ninguem precisar saber disso.
    """
    from app.infra.repositorios.cadastro import _INDICE_CTS, _INDICE_SUBBACIA

    linhas = await con.fetch(
        f"SELECT * FROM {_i()}.{tabela} WHERE {chave} = $1", ficha_id
    )
    pos = _INDICE_CTS if "cts" in tabela else _INDICE_SUBBACIA
    atual: dict[str, dict[str, Any]] = {}
    for l in linhas:
        i = pos.get(l["componente"])
        if i is not None:
            atual[i] = {
                "nome": l["componente"],
                **{campo: l[col] for campo, col in OBRA.items() if col in l},
            }
    return atual


async def _gravar_obras(
    con: Any,
    *,
    tabela: str,
    chave: str,
    ficha_id: str,
    obras: list[dict[str, Any]],
    atual: dict[str, dict[str, Any]],
) -> list[Alteracao]:
    """As obras da ficha, substituídas em bloco.

    `capex` não vem do corpo: é derivado (`capex`) porque a tela não o manda e
    porque o motor não o leria de qualquer forma. Calcular no servidor mantém uma
    conta só — se os dois lados calculassem, divergiriam por arredondamento e
    ninguém saberia qual está no plano.

    `anoObrig` e `proibAte` são CÓDIGOS, não anos quaisquer (`0` = sem restrição,
    `-1` = obrigatória em qualquer ano). Por isso vão como vieram, sem `or 0`:
    tratar ausência como zero afirmaria "sem restrição" onde a resposta é silêncio.

    ## O diff sai ANTES do `DELETE`, e é por isso que ele existe aqui

    A gravação é `DELETE` + `INSERT` do bloco inteiro, e depois do `DELETE` não há
    com o que comparar: a informação de quem mudou o quê desaparece com as linhas.
    Por isso `atual` — o que `_obras_gravadas` já tinha lido para materializar a
    ficha — entra como parâmetro em vez de ser relido: é o mesmo retrato, dentro
    da mesma transação, e reler abriria janela para ele mudar no meio.

    O campo na trilha é `obra:<componente>:<campo>` porque a obra não tem
    identidade própria na tela — quem a identifica é o NOME do componente, que é o
    que a pessoa lê na linha da tabela. Índice (`obra:2:qtd`) seria mais curto e
    não diria nada a quem consulta a auditoria seis meses depois.
    """
    novas = {
        str(i): {"nome": o.get("nome"), **{k: valor_de_obra(o, k) for k in OBRA}}
        for i, o in enumerate(obras)
    }
    mudancas: list[Alteracao] = []
    for indice in sorted(set(atual) | set(novas), key=int):
        antiga = atual.get(indice) or {}
        nova = novas.get(indice) or {}
        nome = nova.get("nome") or antiga.get("nome") or f"índice {indice}"
        mudancas += diferencas(
            {k: antiga.get(k) for k in OBRA},
            {k: nova.get(k) for k in OBRA},
            prefixo=f"obra:{nome}:",
            # Obra é cadastro da Regional inteiro: não há número de obra vindo do
            # Databricks para "corrigir".
            origem=REGIONAL,
        )

    await con.execute(f"DELETE FROM {_i()}.{tabela} WHERE {chave} = $1", ficha_id)
    if not obras:
        return mudancas

    colunas = [chave, "componente", *OBRA.values(), "capex"]
    marc = ", ".join(f"${i + 1}" for i in range(len(colunas)))
    linhas = [
        (ficha_id, o.get("nome"), *[valor_de_obra(o, k) for k in OBRA], capex(o))
        for o in obras
    ]
    await con.executemany(
        f"INSERT INTO {_i()}.{tabela} ({', '.join(colunas)}) VALUES ({marc})", linhas
    )
    return mudancas


async def _gravar_coleta(
    con: Any,
    *,
    tabela: str,
    chave: str,
    ficha_id: str,
    params: dict[str, Any],
    bloco_db: dict[str, Any],
) -> list[Alteracao]:
    """A ficha de coleta (sub-bacia ou CTS) — os dois blocos na mesma linha.

    `params` viaja sempre inteiro, inclusive `popU`/`popA`. A régua de cobertura da
    cidade decide se esses dois APARECEM na tela e se contam pendência; não se são
    gravados. Trocar a régua de uma cidade não pode apagar o que alguém preencheu.

    Devolve o que MUDOU. A leitura de antes acontece dentro da mesma transação e
    depois do lock da ficha (`salvar_coleta`), então ninguém escreve entre ler e
    comparar — sem isso a trilha registraria como "de X" um X que já não era o
    valor no instante da gravação.
    """
    juntos = {**bloco_db, **params}
    frente_para_coluna = {v: k for k, v in COLETA.items()}
    colunas = [
        frente_para_coluna[k]
        for k in juntos
        if k in frente_para_coluna and k not in NAO_MODELADOS
    ]
    if not colunas:
        return []
    valores = [numerico(juntos[COLETA[c]], COLETA[c]) for c in colunas]

    # O ANTES, pelas mesmas colunas que vão ser escritas. Ficha que ainda não
    # existe devolve linha nenhuma, e aí todo campo preenchido é criação — que é
    # a leitura certa, e é diferente de "mudou de vazio para X".
    linha = await con.fetchrow(
        f"SELECT {', '.join(colunas)} FROM {_i()}.{tabela} WHERE {chave} = $1",
        ficha_id,
    )
    antes = {COLETA[c]: (linha[c] if linha else None) for c in colunas}

    marc = ", ".join(f"${i + 2}" for i in range(len(colunas)))
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(colunas))
    # `RETURNING`, e o `depois` sai DAQUI — não do corpo que chegou. A diferença
    # aparece sempre que o banco coage o valor: um decimal numa coluna inteira
    # fazia a trilha registrar `3,7` enquanto a coluna guardava `3`, ou seja,
    # auditoria afirmando um número que nunca existiu. E, como o gravado nunca
    # alcançava o enviado, TODA gravação parecia mudança e a trilha crescia a cada
    # salvamento.
    #
    # Com os dois lados vindo do banco, `igual` compara iguais com iguais. Vale
    # para qualquer coerção, inclusive as que ninguém mapeou — normalizar em
    # Python exigiria duplicar as regras do Postgres aqui, e esquecer uma faria a
    # trilha voltar a mentir.
    gravada = await con.fetchrow(
        f"""INSERT INTO {_i()}.{tabela} ({chave}, {", ".join(colunas)})
            VALUES ($1, {marc})
            ON CONFLICT ({chave}) DO UPDATE SET {sets}
            RETURNING {", ".join(colunas)}""",
        ficha_id,
        *valores,
    )
    depois = {COLETA[c]: gravada[c] for c in colunas}
    return diferencas(antes, depois, origem=origem_do_campo)


# ---------------------------------------------------------------- pertencimento
#: De que unidade e cada tipo de ficha. O `unidade_id` do CAMINHO nao pode ser
#: acreditado: ele so dizia em nome de quem gravar, e nao QUE ficha podia ser
#: gravada — dava para escrever na sub-bacia de outra unidade so trocando o id da
#: URL, e a trilha ainda registrava a unidade errada como dona.
_DONO = {
    # A empresa e o primeiro nivel abaixo da unidade: ela carrega `unidade_id`
    # na propria linha, sem precisar subir por ninguem.
    "empresa": """
        SELECT e.unidade_id FROM {i}.empresa e WHERE e.emp_codigo = $1""",
    "sub-bacia": """
        SELECT s.unidade_id
          FROM {i}.sistema_topologia t
          JOIN {i}.cidade_sistema cs USING (sistema_id)
          JOIN {i}.cidade_empresa c ON c.cidade_id = cs.cidade_id
          JOIN {i}.empresa s USING (emp_codigo)
         WHERE t.componente_sistema_id = $1""",
    "cidade": """
        SELECT s.unidade_id
          FROM {i}.cidade_empresa c
          JOIN {i}.empresa s USING (emp_codigo)
         WHERE c.cidade_id = $1""",
    # A CTS percorre o MESMO caminho da sub-bacia: quem diz de que unidade ela e
    # e o SISTEMA em que ela foi colocada. Antes o caminho passava por
    # `subbacia_cts` — o par com a sub-bacia —, e isso dizia a unidade errada por
    # duas vias: uma CTS sem par nao pertencia a unidade nenhuma (e nao dava para
    # editar a ficha dela), e uma pareada herdava a unidade da IRMA, mesmo estando
    # num sistema de outra.
    #
    # CTS fora de sistema nao tem unidade, e por isso nao passa aqui. E o certo:
    # ela tambem nao aparece no Grupo 05, que so lista as colocadas. Adiciona-la a
    # um sistema (Grupo 01) e o que a torna editavel.
    "cts": """
        SELECT s.unidade_id
          FROM {i}.sistema_topologia t
          JOIN {i}.cts_operacional o ON o.cts = t.componente_sistema_id
          JOIN {i}.cidade_sistema cs USING (sistema_id)
          JOIN {i}.cidade_empresa c ON c.cidade_id = cs.cidade_id
          JOIN {i}.empresa s USING (emp_codigo)
         WHERE t.componente_sistema_id = $1""",
    # A ETE percorre o MESMO caminho da sub-bacia, e nao um caminho proprio: em
    # `sistema_topologia` ela e um componente do sistema como qualquer outro. O que
    # a distingue e o id dela tambem existir em `ete_capex` — e assim que o motor a
    # identifica (`otimizador_capex_v62.py:1111`):
    #
    #     if comp in ete_ids: ete_do_sis[d["sistema_id"]] = comp
    #
    # A ETE chega a unidade pelo MESMO caminho da sub-bacia: ela e um componente
    # de `sistema_topologia`. O `JOIN` com `ete_capex` no fim garante que o id
    # pedido e mesmo uma ETE, e nao uma sub-bacia entrando pela rota errada.
    "ete": """
        SELECT s.unidade_id
          FROM {i}.sistema_topologia t
          JOIN {i}.ete_capex e ON e.ete_id = t.componente_sistema_id
          JOIN {i}.cidade_sistema cs USING (sistema_id)
          JOIN {i}.cidade_empresa c ON c.cidade_id = cs.cidade_id
          JOIN {i}.empresa s USING (emp_codigo)
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


async def _marcar_autoria(
    con: Any, *, tabela: str, chave: str, ficha_id: str, autor: str
) -> dict[str, str]:
    """Quem gravou esta ficha, e quando. Em TODA gravação.

    **O autor vem do TOKEN, e o parâmetro `autor` é o mesmo que a trilha usa
    (`_registrar`).** Nunca do corpo: um cliente que pudesse escolher o nome que
    assina transformaria a auditoria em decoração.

    `now()` e não `clock_timestamp()`: dentro da transação, `now()` é o instante
    em que ela COMEÇOU, então a ficha, suas obras e sua trilha ficam com o mesmo
    carimbo. Três horários com milissegundos de diferença para uma gravação só
    fariam parecer que houve três.

    `INSERT ... ON CONFLICT` e não `UPDATE`: a ficha operacional pode não existir
    ainda — o `PUT` de uma sub-bacia que nunca teve linha em
    `subbacia_operacional` a cria. Com `UPDATE`, a primeira gravação de uma ficha
    nova seria justamente a que não deixaria rastro.

    **Devolve o carimbo** porque a resposta do `PUT` o leva de volta para a tela:
    sem isso a ficha exibiria a alteração anterior logo depois de você salvar, até
    alguém recarregar.
    """
    linha = await con.fetchrow(
        f"""INSERT INTO {_i()}.{tabela} ({chave}, atualizado_em, atualizado_por)
            VALUES ($1, now(), $2)
            ON CONFLICT ({chave}) DO UPDATE
              SET atualizado_em  = EXCLUDED.atualizado_em,
                  atualizado_por = EXCLUDED.atualizado_por
            RETURNING atualizado_em, atualizado_por""",
        ficha_id,
        autor,
    )
    from app.infra.repositorios.cadastro import _auditoria

    return _auditoria(dict(linha))


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
        # O lock SERIALIZA os PUTs da mesma ficha — ele ordena, nao recusa.
        # Sem ele, duas gravacoes simultaneas intercalam o `DELETE`+`INSERT` das
        # obras e a ficha termina com metade de cada uma.
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", ficha_id)
        exigir_ficha_inteira(corpo)
        bloco_db = corpo.get("db") or {}
        # GRAVAR A FICHA DE UMA MACRORREGIÃO REFAZ A SOMA.
        #
        # O bloco `db` que chega veio do último `GET`, e o `GET` mostra a linha
        # gravada — que pode estar para trás de uma recarga do Databricks
        # (`pendencias._macrorregioes_desatualizadas` é quem avisa). Regravá-lo
        # como veio devolveria ao banco a soma velha, e a divergência
        # sobreviveria à única ação que a pessoa tem para corrigi-la.
        #
        # As medidas do Databricks não são digitadas: são travadas na tela. Não
        # há, portanto, nada que a pessoa tenha escrito aqui para ser descartado —
        # o que se descarta é uma cópia envelhecida do que a base comercial já diz.
        # A trilha registra a diferença com origem `databricks`, que é o que ela
        # significa: correção de número que veio de fora.
        if e_cts and (frescas := await _somas_de_hoje(con, ficha_id)) is not None:
            bloco_db = frescas
        mudancas = await _gravar_coleta(
            con,
            tabela=tabela,
            chave=chave,
            ficha_id=ficha_id,
            params=corpo.get("params") or {},
            bloco_db=bloco_db,
        )
        # `in` e não `or []`: ficha SEM a chave não mexe nas obras; ficha COM a
        # chave e lista vazia apaga todas. São intenções diferentes.
        if "obrasOverride" in corpo:
            gravadas = await _obras_gravadas(con, tab_obra, chave, ficha_id)
            mudancas += await _gravar_obras(
                con,
                tabela=tab_obra,
                chave=chave,
                ficha_id=ficha_id,
                obras=obras_da_ficha(
                    corpo.get("obrasOverride"),
                    gravadas,
                    esperadas=OBRAS_CTS if e_cts else OBRAS_SUBBACIA,
                    rotulo=tipo,
                ),
                atual=gravadas,
            )
        n = await _registrar(
            con,
            tipo=tipo,
            ficha_id=ficha_id,
            unidade_id=unidade_id,
            autor=autor,
            mudancas=mudancas,
        )
        auditoria = await _marcar_autoria(
            con, tabela=tabela, chave=chave, ficha_id=ficha_id, autor=autor
        )
    return {"id": ficha_id, "alteracoesGravadas": n, **auditoria}


async def _diff_da_cidade(
    con: Any, cidade_id: str, corpo: dict[str, Any]
) -> list[Alteracao]:
    """O que muda na ficha de cidade — as três tabelas, antes de qualquer escrita.

    Tem de rodar ANTES, e não depois: metas e faixas são apagadas e reinseridas em
    bloco, e depois do `DELETE` não sobra com o que comparar.

    ## Metas e faixas são COLEÇÕES, e por isso a chave importa

    A meta é identificada pelo ANO, e a faixa pela COBERTURA — não pela posição na
    lista. Comparar por posição diria que remover a primeira meta mudou todas as
    outras, quando o que houve foi uma remoção só.

    Com a chave certa, a leitura sai limpa nos três casos, e a convenção de NULL
    da migração 007 dá conta dos dois extremos:

        meta:2030:pct   ""   -> "85"    a meta passou a existir
        meta:2030:pct   "80" -> "85"    o valor mudou
        meta:2030:pct   "80" -> NULL    a meta foi removida
    """
    mudancas: list[Alteracao] = []
    cidade = corpo.get("cidade") or {}

    # A CONCESSAO NAO ENTRA NESTE DIFF, e a ausencia e o ponto: ela e da empresa,
    # e quem registra a mudanca dela e `salvar_empresa`. Compara-la aqui, contra
    # um corpo que nao manda mais `fim`, escreveria na trilha uma remocao
    # (`2045 -> NULL`) a cada gravacao de cidade — enquanto o upsert abaixo
    # preserva o valor. Trilha que afirma o que nao aconteceu e pior que trilha
    # que nao afirma nada.
    # A CIDADE NAO TEM MAIS CAMPO PROPRIO NESTA FICHA. `unidade_cobertura` saiu
    # (migracao 019): a regua da cobertura virou parametro de rodada. O que sobra
    # aqui sao as metas e as faixas, comparadas logo abaixo.

    if "metas" in corpo:
        antes = {
            texto_trilha(l["ano"], "ano"): l["cobertura_pct"]
            for l in await con.fetch(
                f"SELECT ano, cobertura_pct FROM {_i()}.metas_cobertura WHERE cidade_id = $1",
                cidade_id,
            )
        }
        depois = {
            texto_trilha(numerico(m.get("ano"), "meta.ano"), "ano"): numerico(
                m.get("pct"), "meta.pct"
            )
            for m in corpo.get("metas") or []
        }
        mudancas += [
            Alteracao(f"meta:{a.campo}:pct", a.antes, a.depois, REGIONAL)
            for a in diferencas(antes, depois, origem=REGIONAL)
        ]

    if "fator" in corpo:
        antes = {
            texto_trilha(l["cobertura_pct"]): l["paridade"]
            for l in await con.fetch(
                f"SELECT cobertura_pct, paridade FROM {_i()}.fator_esgoto WHERE cidade_id = $1",
                cidade_id,
            )
        }
        depois = {
            texto_trilha(numerico(f.get("cob"), "fator.cob")): numerico(
                f.get("par"), "fator.par"
            )
            for f in corpo.get("fator") or []
        }
        mudancas += [
            Alteracao(f"faixa:{a.campo}:paridade", a.antes, a.depois, REGIONAL)
            for a in diferencas(antes, depois, origem=REGIONAL)
        ]

    return mudancas


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
        mudancas = await _diff_da_cidade(con, cidade_id, corpo)

        # A CONCESSAO E ESCRITA NA EMPRESA (`PUT /empresas/{emp_codigo}`), nunca
        # aqui: e a empresa que assina o contrato, e o gatilho
        # `empresa_propaga_concessao` desce o ano para os municipios dela.
        #
        # Por isso o upsert abaixo PRESERVA o valor que a cidade ja tem, em vez
        # de reescreve-lo com o que veio no corpo.
        await con.execute(
            f"""INSERT INTO {_i()}.cidade_operacional
                    (cidade_id, data_fim_concessao)
                VALUES ($1, $2)
                ON CONFLICT (cidade_id) DO UPDATE
                  SET data_fim_concessao =
                        COALESCE({_i()}.cidade_operacional.data_fim_concessao,
                                 EXCLUDED.data_fim_concessao)""",
            cidade_id,
            None,
        )
        if "metas" in corpo:
            await con.execute(
                f"DELETE FROM {_i()}.metas_cobertura WHERE cidade_id = $1", cidade_id
            )
            await con.executemany(
                f"""INSERT INTO {_i()}.metas_cobertura (cidade_id, ano, cobertura_pct)
                    VALUES ($1, $2, $3)""",
                [
                    (cidade_id, numerico(m.get("ano"), "meta.ano"),
                     numerico(m.get("pct"), "meta.pct"))
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
                        numerico(f.get("cob"), "fator.cob"),
                        numerico(f.get("par"), "fator.par"),
                    )
                    for f in corpo.get("fator") or []
                ],
            )
        n = await _registrar(
            con,
            tipo="cidade",
            ficha_id=cidade_id,
            unidade_id=unidade_id,
            autor=autor,
            mudancas=mudancas,
        )
        # A ficha de cidade sai de três tabelas e o carimbo mora só em
        # `cidade_operacional`. É de propósito: quem editou uma meta editou a ficha
        # da cidade, e é a ficha que a tela mostra. Três carimbos separados
        # responderiam uma pergunta que ninguém faz.
        auditoria = await _marcar_autoria(
            con,
            tabela="cidade_operacional",
            chave="cidade_id",
            ficha_id=cidade_id,
            autor=autor,
        )
    return {"id": cidade_id, "alteracoesGravadas": n, **auditoria}


def _nova_paratexto(v: Any) -> Any:
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
        ete["nova"] = _nova_paratexto(ete["nova"])
    presentes = [k for k in ETE if k in ete]
    async with db.transacao() as con:
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", ete_id)
        mudancas: list[Alteracao] = []
        if presentes:
            colunas = [ETE[k] for k in presentes]
            valores = [
                numerico(ete[k], f"ete.{k}") if k in ETE_NUM else ete[k]
                for k in presentes
            ]
            # O upsert toca SÓ os campos presentes, e o diff segue a mesma régua:
            # campo que o corpo não trouxe não foi alterado, e afirmar que foi
            # encheria a trilha de mudanças que ninguém fez.
            linha = await con.fetchrow(
                f"SELECT {', '.join(colunas)} FROM {_i()}.ete_capex WHERE ete_id = $1",
                ete_id,
            )
            mudancas = diferencas(
                {k: (linha[ETE[k]] if linha else None) for k in presentes},
                dict(zip(presentes, valores, strict=True)),
                origem=REGIONAL,
            )
            marc = ", ".join(f"${i + 2}" for i in range(len(colunas)))
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(colunas))
            await con.execute(
                f"""INSERT INTO {_i()}.ete_capex (ete_id, {", ".join(colunas)})
                    VALUES ($1, {marc})
                    ON CONFLICT (ete_id) DO UPDATE SET {sets}""",
                ete_id,
                *valores,
            )
        n = await _registrar(
            con,
            tipo="ete",
            ficha_id=ete_id,
            unidade_id=unidade_id,
            autor=autor,
            mudancas=mudancas,
        )
        auditoria = await _marcar_autoria(
            con, tabela="ete_capex", chave="ete_id", ficha_id=ete_id, autor=autor
        )
    return {"id": ete_id, "alteracoesGravadas": n, **auditoria}


# ----------------------------------------------------------------- EMPRESA
async def salvar_empresa(
    *, unidade_id: str, emp_codigo: str, corpo: dict[str, Any], autor: str
) -> dict[str, Any]:
    """PUT da ficha de empresa: hoje, o fim da concessao.

    A CONCESSAO E DA EMPRESA, e este e o unico lugar que a escreve.
    E O UNICO CAMINHO DE ESCRITA do campo. A aba de municipio mostra o ano, mas
    nao o grava: dois caminhos para o mesmo dado divergem no dia em que um deles
    muda.

    Quem espalha o valor para os municipios da empresa e o BANCO, pelo gatilho
    `empresa_propaga_concessao` (migracao 015) — nao este codigo. A carga do
    Databricks tambem escreve nesta tabela, e propagar aqui deixaria a cidade
    com o prazo antigo sempre que a empresa chegasse por fora da aplicacao.
    """
    await exigir_dona("empresa", emp_codigo, unidade_id)
    empresa = corpo.get("empresa") or {}
    if "fim" not in empresa:
        return {"id": emp_codigo, "alteracoesGravadas": 0}

    fim = numerico(empresa.get("fim"), "empresa.fim")
    async with db.transacao() as con:
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", emp_codigo)
        linha = await con.fetchrow(
            f"SELECT data_fim_concessao FROM {_i()}.empresa WHERE emp_codigo = $1",
            emp_codigo,
        )
        mudancas = diferencas(
            {"fim": linha["data_fim_concessao"] if linha else None},
            {"fim": fim},
            origem=REGIONAL,
        )
        await con.execute(
            f"""UPDATE {_i()}.empresa SET data_fim_concessao = $2
                 WHERE emp_codigo = $1""",
            emp_codigo,
            fim,
        )
        n = await _registrar(
            con,
            tipo="empresa",
            ficha_id=emp_codigo,
            unidade_id=unidade_id,
            autor=autor,
            mudancas=mudancas,
        )
    return {"id": emp_codigo, "alteracoesGravadas": n}


# ---------------------------------------------------------------- TOPOLOGIA
# O caminho ate a ETE, e em que sistema cada componente entra.
#
# ISTO NAO VEM DO DATABRICKS. De fora vem quais sub-bacias e qual ETE pertencem
# ao sistema, e todas as CTS cadastradas. Quem monta o sistema — quem liga cada
# componente ao seguinte ate a ETE, e em que sistema cada CTS entra — e a
# Regional. Ate aqui a tela do Grupo 01 editava contra o `sessionStorage` do
# navegador e avisava, em letras, que nada daquilo chegava ao cadastro.
#
# POR QUE A VALIDACAO E MAIS DURA QUE NAS OUTRAS FICHAS. Preco errado sai errado na
# conta e alguem estranha o numero. Caminho errado nao aparece: o motor percorre
# `jusante` ate a lista acabar (`caminho()`, otimizador_capex_v62.py) e, se o
# caminho nao chega na ETE, ele simplesmente NAO soma as obras de transporte
# daquele trecho — o plano sai mais barato e continua plausivel. Um ciclo e pior:
# o laco tem trava em 200 saltos, entao ele nao trava a maquina, ele repete o mesmo
# trecho ate 200 vezes e infla os requisitos. Nenhum dos dois levanta erro.
#
# Por isso o que e INCOERENTE e recusado aqui (ciclo, jusante em outro sistema,
# jusante em si mesmo, ETE com jusante, segunda ETE no sistema), e o que e apenas
# INCOMPLETO passa: durante a montagem o caminho fica pela metade o tempo todo, e
# recusar isso impediria de montar. "Chegou na ETE?" e pergunta de prontidao, e
# nao de gravacao.


#: Componente ja conhecido do cadastro? Um id que nao esta em lugar nenhum seria um
#: no INVENTADO: o motor o trataria como no de demanda ZERO, sem ficha e sem obras,
#: e ele entraria no caminho ate a ETE sem nunca aparecer numa tela. Foi assim que o
#: antigo `POST /cts` produziu 339 fichas para 337 nos — ele gravava ficha e par sem
#: tocar na topologia, o espelho exato deste erro.
_EXISTE_COMPONENTE = """
    SELECT 1 FROM {i}.sistema_topologia WHERE componente_sistema_id = $1
    UNION ALL SELECT 1 FROM {i}.subbacia_operacional WHERE sub_bacia = $1
    UNION ALL SELECT 1 FROM {i}.cts_operacional      WHERE cts       = $1
    UNION ALL SELECT 1 FROM {i}.ete_capex            WHERE ete_id    = $1
    LIMIT 1"""


async def _travar_sistemas(con: Any, *sistemas: str | None) -> None:
    """Serializa TODA escrita que toca estes sistemas.

    O lock é do SISTEMA, e não do componente, porque as regras são do sistema:
    "um caminho sem ciclo", "uma ETE só", "uma CTS quando marcado" — todas se
    verificam olhando os OUTROS componentes. Travando só o componente alterado,
    duas requisições liam o estado antigo uma da outra e as duas passavam:

      A → B e B → A ao mesmo tempo    fecham um ciclo que nenhuma das duas viu
      A → B e B mudando de sistema    deixam A escoando para fora do sistema
      duas CTS no mesmo sistema       furam o limite de uma

    Nenhum dos três levanta erro depois: o motor segue `jusante` com trava em 200
    saltos e nunca confere fronteira nem contagem.

    ORDENADO, e sem repetição: mover um componente entre sistemas tranca os dois,
    e duas requisições que trancassem o mesmo par em ordens opostas travariam uma
    na outra. `sorted` dá a ordem global que evita isso. O lock é `xact`: sai
    sozinho no fim da transação, com commit ou rollback.

    SEMPRE DEPOIS de `_travar_unidade`, quando os dois entram na mesma transação:
    é a ordem global que impede o abraço entre uma gravação de topologia e a
    marcação da unidade.
    """
    for sistema in sorted({s for s in sistemas if s}):
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", sistema)


async def _travar_unidade(con: Any, unidade_id: str) -> None:
    """Serializa a POLÍTICA DE CTS da unidade contra as gravações de topologia.

    A regra "uma CTS por sistema" passou a ser da unidade, e com isso ela deixou
    de caber num lock de sistema: marcar a unidade precisa saber que NENHUM
    sistema dela ganhou uma segunda CTS no meio do caminho, e travar os 474
    sistemas de uma unidade grande um a um seria pagar 474 idas ao banco por um
    clique numa caixa.

    Então o lock é da UNIDADE, e quem grava topologia toma os dois: a unidade
    primeiro, os sistemas depois. É uma ordem global — nenhuma transação toma um
    sistema antes da unidade —, e é ela que impede o abraço.

    O espaço de hash é o mesmo dos sistemas, e uma colisão entre um id de unidade
    e um de sistema é possível. O efeito de uma colisão aqui é serialização a
    mais, nunca resultado errado.
    """
    await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", unidade_id)


async def _empresa_do_sistema(con: Any, sistema_id: str) -> str | None:
    """A empresa que opera o sistema — pela cidade dele, como toda empresa aqui."""
    return await con.fetchval(
        f"""SELECT ce.emp_codigo
              FROM {_i()}.cidade_sistema cs
              JOIN {_i()}.cidade_empresa ce ON ce.cidade_id = cs.cidade_id
             WHERE cs.sistema_id = $1""",
        sistema_id,
    )


async def _empresa_da_macrorregiao(con: Any, cts_id: str) -> str | None:
    """A empresa da LINHA da macrorregião, ou `None` se `cts_id` não é uma.

    Pela cidade dominante dela — que é de um membro, e todo membro é da empresa
    do par. É a mesma leitura que `_somas_de_hoje` faz para recortar os membros.
    """
    return await con.fetchval(
        f"""SELECT ce.emp_codigo
              FROM {_i()}.cts_operacional o
              JOIN {_i()}.cidade_empresa ce ON ce.cidade_id = o.cidade_id
             WHERE o.cts = $1 AND o.e_macrorregiao""",
        cts_id,
    )


async def _exigir_empresa_da_macrorregiao(
    con: Any, *, componente_id: str, sistema_id: str
) -> None:
    """A MACRORREGIÃO SÓ ENTRA EM SISTEMA DA EMPRESA DELA.

    A chave da macrorregião é `(sistema_cts, emp_codigo)`, e a segunda metade
    não é decoração: a tela recorta o seletor por ela, e uma regra que só a tela
    conhece é uma regra que a lista antiga aberta — ou uma chamada direta —
    atravessa. Colocada num sistema de outra empresa, a macrorregião somaria os
    coletores de uma operadora dentro do sistema de outra, e a rodada atribuiria
    receita e obras a quem não as opera.

    Não se aplica ao coletor comum: o recorte dele é por CIDADE, e a gravação
    nunca o impôs — é escolha de quem monta, e a topologia tem componente de
    outra cidade em estado legado. A macrorregião é diferente porque a empresa é
    parte do que ela É.
    """
    empresa = await _empresa_da_macrorregiao(con, componente_id)
    if empresa is None:
        return
    do_sistema = await _empresa_do_sistema(con, sistema_id)
    if do_sistema != empresa:
        raise TopologiaInvalida(
            f"A macrorregião {componente_id!r} é da empresa {empresa!r}, e o sistema "
            f"{sistema_id!r} é de {do_sistema!r}. Uma macrorregião só entra em "
            "sistema da empresa que a opera — é a outra metade da chave dela."
        )


async def _unidade_do_sistema(con: Any, sistema_id: str) -> str | None:
    linha = await con.fetchrow(
        f"""SELECT s.unidade_id
              FROM {_i()}.cidade_sistema cs
              JOIN {_i()}.cidade_empresa c ON c.cidade_id = cs.cidade_id
              JOIN {_i()}.empresa s USING (emp_codigo)
             WHERE cs.sistema_id = $1""",
        sistema_id,
    )
    return linha["unidade_id"] if linha else None


async def _cts_do_sistema(con: Any, sistema_id: str, exceto: str = "") -> list[str]:
    """As CTS já colocadas neste sistema, fora a que está sendo gravada."""
    linhas = await con.fetch(
        f"""SELECT t.componente_sistema_id AS id
              FROM {_i()}.sistema_topologia t
              JOIN {_i()}.cts_operacional o ON o.cts = t.componente_sistema_id
             WHERE t.sistema_id = $1 AND t.componente_sistema_id <> $2
             ORDER BY 1""",
        sistema_id,
        exceto,
    )
    return [l["id"] for l in linhas]


async def _unidade_usa_cts(con: Any, unidade_id: str) -> bool:
    """A unidade usa MACRORREGIÃO DE CTS — e com isso cada sistema dela aceita uma?

    A pergunta era do sistema e passou a ser da unidade: a política é uma, e vale
    para todos os sistemas dentro dela.
    """
    linha = await con.fetchrow(
        f"SELECT usa_macrorregiao_cts FROM {_i()}.unidade_regional WHERE unidade_id = $1",
        unidade_id,
    )
    return bool(linha and linha["usa_macrorregiao_cts"])


async def _e_ete(con: Any, componente_id: str) -> bool:
    return bool(
        await con.fetchrow(
            f"SELECT 1 FROM {_i()}.ete_capex WHERE ete_id = $1", componente_id
        )
    )


async def _e_cts(con: Any, componente_id: str) -> bool:
    return bool(
        await con.fetchrow(
            f"SELECT 1 FROM {_i()}.cts_operacional WHERE cts = $1", componente_id
        )
    )


async def _quem_aponta_para(con: Any, componente_id: str) -> list[str]:
    """Componentes cujo jusante é este. Tirar o alvo do sistema os deixaria pendurados."""
    linhas = await con.fetch(
        f"""SELECT componente_sistema_id AS id FROM {_i()}.sistema_topologia
             WHERE componente_sistema_id_jusante = $1
             ORDER BY 1""",
        componente_id,
    )
    return [l["id"] for l in linhas]


#: As colunas que a linha da macrorregião recebe ao nascer, na ordem do `INSERT`.
#:
#: São as medidas somadas (`COLUNAS_QUE_SOMAM`), mais `cidade_id` — a dominante.
#: Os `params` FICAM NULOS de propósito: são preenchimento da Regional, e a
#: macrorregião nasce com o mesmo nada de qualquer ficha nova — pendência que a
#: tela vai cobrar, e não um valor inventado a partir dos membros.
_COLUNAS_DA_LINHA_NOVA = macrorregiao_cts.COLUNAS_QUE_SOMAM + ("cidade_id",)


#: TUDO o que a obra da macrorregião recebe ao nascer: nome e unidade de medida.
#:
#: É a fronteira entre vocabulário e medida, e por isso é uma constante e não um
#: literal dentro do SQL: uma coluna a mais aqui é a base literal voltando, e uma
#: constante dá onde apontar o teste (`test_obras_do_banco`). Qualquer número
#: nasce NULO — quem preenche é a Regional.
COLUNAS_DA_OBRA_NOVA = ("cts", "componente", "unidade")


async def _membros_da_macrorregiao(
    con: Any, unidade_id: str, macro: str
) -> list[dict[str, Any]]:
    """Os coletores que formam `macro` nesta unidade — vazio se `macro` não nomeia uma.

    `NOT e_macrorregiao` não é zelo: a própria linha da macrorregião tem
    `sistema_cts` NULO (migração 021) e já não cairia aqui. A cláusula está escrita
    porque é ela que garante isso para quem ler a consulta sozinha.

    `colocada` vem junto para a pergunta "está livre?" não precisar de uma segunda
    ida ao banco dentro da transação já travada.

    `emp_codigo` vem junto porque a chave da macrorregião é o PAR
    `(sistema_cts, emp_codigo)`: o nome sozinho pode pertencer a duas empresas da
    unidade, e quem chama precisa saber disso para recusar em vez de somar as
    duas. Ver `macrorregiao_cts.nomes_ambiguos`.
    """
    linhas = await con.fetch(
        f"""WITH cid AS ({CIDADES_DA_UNIDADE.format(i=_i())})
            SELECT o.*, cid.emp_codigo,
                   coalesce(t.sistema_id, '') <> '' AS colocada
              FROM {_i()}.cts_operacional o
              JOIN cid ON cid.cidade_id = o.cidade_id
              LEFT JOIN {_i()}.sistema_topologia t
                     ON t.componente_sistema_id = o.cts
             WHERE o.sistema_cts = $2 AND NOT o.e_macrorregiao
             ORDER BY o.cts""",
        unidade_id,
        macro,
    )
    return [dict(l) for l in linhas]


async def _somas_de_hoje(con: Any, ficha_id: str) -> dict[str, Any] | None:
    """As medidas do Databricks somadas AGORA, ou `None` se a ficha não é macro.

    Devolve com os nomes do FRONT (`fat`, `ligU`, …), porque é isso que
    `_gravar_coleta` recebe no bloco `db` — assim a gravação, a comparação e a
    trilha continuam sendo as de sempre, e não um caminho paralelo.

    `populacao_novas_obras` fica de fora: ela está em `NAO_MODELADOS`, e a escrita
    nunca a toca. Refrescá-la aqui abriria exceção numa regra que vale para as
    duas tabelas de coleta, para ganhar uma coluna que o motor deriva de
    `universo - atuais` de qualquer jeito.
    """
    linha = await con.fetchrow(
        f"SELECT e_macrorregiao FROM {_i()}.cts_operacional WHERE cts = $1", ficha_id
    )
    if not linha or not linha["e_macrorregiao"]:
        return None
    # A CHAVE É O PAR, TAMBÉM AQUI. Somar todo mundo que tem aquele `sistema_cts`
    # é somar por NOME, e nome não é chave: bastaria uma recarga dar o mesmo nome
    # a coletores de outra empresa para esta gravação substituir a ficha pela soma
    # de um grupo que não é o dela. A empresa da macrorregião vem da cidade dela,
    # e é ela que recorta os membros.
    membros = await con.fetch(
        f"""WITH minha AS (
                SELECT ce.emp_codigo
                  FROM {_i()}.cts_operacional o
                  JOIN {_i()}.cidade_empresa ce ON ce.cidade_id = o.cidade_id
                 WHERE o.cts = $1 AND o.e_macrorregiao
            )
            SELECT o.*
              FROM {_i()}.cts_operacional o
              JOIN {_i()}.cidade_empresa ce ON ce.cidade_id = o.cidade_id
              JOIN minha ON minha.emp_codigo = ce.emp_codigo
             WHERE o.sistema_cts = $1 AND NOT o.e_macrorregiao""",
        ficha_id,
    )
    if not membros:
        # Sem coletor nenhum não há soma. A ficha continua como está — o que ela
        # afirma foi somado um dia, e zerá-la agora apagaria dado por causa de uma
        # coluna que a origem deixou de mandar. Quem denuncia esse estado é
        # `pendencias._macrorregioes_desatualizadas`.
        return None
    somado = macrorregiao_cts.agregar([dict(m) for m in membros])
    return {
        COLETA[coluna]: valor
        for coluna, valor in somado.items()
        if coluna in COLETA and COLETA[coluna] not in NAO_MODELADOS
    }


async def _nomes_de_macrorregiao(con: Any, unidade_id: str) -> set[str]:
    """Os nomes que, nesta unidade, são de macrorregião — numa consulta só.

    Serve o caminho em LOTE, onde a alternativa é perguntar por componente
    enviado: o desenho de um sistema chega com dezenas deles, e quase nenhum é
    macrorregião. Uma ida ao banco responde por todos.
    """
    linhas = await con.fetch(
        f"""SELECT DISTINCT o.sistema_cts AS nome
              FROM {_i()}.cts_operacional o
              JOIN ({CIDADES_DA_UNIDADE.format(i=_i())}) c ON c.cidade_id = o.cidade_id
             WHERE o.sistema_cts IS NOT NULL AND btrim(o.sistema_cts) <> ''
               AND NOT o.e_macrorregiao""",
        unidade_id,
    )
    return {l["nome"] for l in linhas}


async def _preparar_macrorregiao(
    con: Any, *, unidade_id: str, componente_id: str, autor: str
) -> None:
    """A MACRORREGIÃO GANHA LINHA NA HORA EM QUE É COLOCADA NUM SISTEMA.

    Até aqui ela era uma soma calculada na leitura: o Grupo 01 a oferece pelo nome
    que a origem deu (`sistema_cts`), e não há linha nenhuma com esse id. Colocar
    exige linha de verdade — as 4 obras são FK para `cts_operacional(cts)`, e o
    motor lê a ficha do nó na própria tabela.

    CRIAR AGORA, E NÃO AO MARCAR A UNIDADE: marcar é declaração de regime, e
    materializar dezenas de agregados que talvez ninguém use encheria a base de
    fichas órfãs — e desmarcar teria de apagá-las, com as obras já preenchidas
    dentro. Colocada, a macrorregião passou a existir para o cadastro, e a linha é
    o registro disso.

    SEGUNDA COLOCAÇÃO NÃO RECRIA: a linha já existe, e `_EXISTE_COMPONENTE` a
    encontra antes de se chegar aqui. Tirar a macrorregião do sistema e devolvê-la
    conserva os `params` e as obras — que é o que se espera de mudar um componente
    de lugar.

    NÃO LEVANTA quando `componente_id` não nomeia macrorregião nenhuma: quem
    responde por id desconhecido é o `_EXISTE_COMPONENTE` de sempre, logo adiante.
    """
    membros = await _membros_da_macrorregiao(con, unidade_id, componente_id)
    if not membros:
        return

    # A CHAVE É O PAR `(sistema_cts, emp_codigo)`, e a linha guarda UM id.
    #
    # Duas empresas da unidade com uma macrorregião de mesmo nome são DUAS
    # macrorregiões, e `cts_operacional.cts` só comporta uma. Somar as duas
    # juntaria coletores que empresas diferentes operam — e é exatamente o que
    # aconteceria em silêncio se este recorte não existisse, porque a busca dos
    # membros é pelo nome.
    #
    # A tela nem chega a oferecer um nome assim (`macrorregiao_cts.livres`); a
    # recusa aqui é para quem chama a rota direto, e para o dia em que a carga
    # criar a ambiguidade com a lista já aberta.
    empresas = sorted({m["emp_codigo"] for m in membros})
    if len(empresas) > 1:
        raise TopologiaInvalida(
            f"{componente_id!r} é o nome de uma macrorregião em mais de uma "
            f"empresa da unidade (" + ", ".join(repr(e) for e in empresas) + "). "
            "São macrorregiões diferentes com o mesmo nome, e o cadastro guarda um "
            "id só — a origem precisa distingui-las antes de elas serem colocadas."
        )

    # COLISÃO DE NOME. Um coletor do Databricks batizado com o nome de uma
    # macrorregião faria esta gravação colocar O COLETOR onde a tela ofereceu a
    # macrorregião — mesmo id, outra coisa. É dado de origem para arrumar, e não
    # ambiguidade para o servidor resolver sozinho escolhendo um dos dois.
    homonimo = await con.fetchrow(
        f"SELECT e_macrorregiao FROM {_i()}.cts_operacional WHERE cts = $1",
        componente_id,
    )
    if homonimo and not homonimo["e_macrorregiao"]:
        raise TopologiaInvalida(
            f"{componente_id!r} é ao mesmo tempo o nome de uma macrorregião e o id "
            "de um coletor do cadastro. Enquanto os dois se chamarem assim, não dá "
            "para saber qual está sendo colocado."
        )
    if homonimo:
        return

    # LIVRE É "NENHUM MEMBRO COLOCADO", a mesma regra pela qual o Grupo 01 a
    # ofereceu. A lista já filtra por isso; a checagem existe porque a lista foi
    # lida antes, e um membro pode ter sido colocado nesse intervalo.
    if presos := [m["cts"] for m in membros if m["colocada"]]:
        raise TopologiaInvalida(
            f"A macrorregião {componente_id!r} não está livre: "
            + ", ".join(repr(c) for c in presos)
            + " já está(ão) em sistema. Tire-o(s) do sistema para colocar a "
            "macrorregião inteira."
        )

    valores = macrorregiao_cts.agregar(membros)
    colunas = ("cts", "e_macrorregiao", *_COLUNAS_DA_LINHA_NOVA)
    lugares = ", ".join(f"${n}" for n in range(1, len(colunas) + 1))
    await con.execute(
        f"""INSERT INTO {_i()}.cts_operacional
                ({", ".join(colunas)}, atualizado_em, atualizado_por)
            VALUES ({lugares}, now(), ${len(colunas) + 1})""",
        componente_id,
        True,
        *(valores.get(c) for c in _COLUNAS_DA_LINHA_NOVA),
        autor,
    )
    # AS QUATRO OBRAS NASCEM JUNTO, VAZIAS.
    #
    # Nascem porque o `PUT` da ficha RECUSA cardinalidade incompleta
    # (`ficha.obras_da_ficha`): ele sobrepõe campo sobre a linha gravada e nunca
    # cria a obra que falta — para uma CTS do Databricks as quatro sempre vieram
    # na carga, e a macrorregião não vem de carga nenhuma. Sem elas, a ficha
    # nasceria impossível de gravar.
    #
    # VAZIAS porque obra NÃO AGREGA. Somar `quantidade` e `preco_unitario` dos
    # membros violaria `capex_e_derivado`, e mediá-los produziria números
    # plausíveis que ninguém digitou — que é o defeito que tirou a "base literal"
    # de `obras_da_ficha`. O que se grava aqui é só o vocabulário: o nome da obra
    # e a unidade de medida dela, iguais nas 337 CTS do banco.
    #
    # A ficha nasce, então, com quatro obras em branco — e a prontidão as cobra
    # como cobra as de qualquer componente recém-colocado.
    await con.executemany(
        f"""INSERT INTO {_i()}.componentes_cts_capex
                ({", ".join(COLUNAS_DA_OBRA_NOVA)})
            VALUES ($1, $2, $3) ON CONFLICT (cts, componente) DO NOTHING""",
        [(componente_id, nome, unidade) for nome, unidade in OBRAS_DA_CTS],
    )

    # A TRILHA REGISTRA A CRIAÇÃO, e não treze campos: quem a lê meses depois quer
    # saber que a macrorregião passou a existir e de que coletores ela veio. Os
    # números somados estão na ficha, e repeti-los aqui seriam treze linhas que
    # ninguém compara com nada. `antes` nulo já diz "não existia" (ver `Alteracao`).
    await _registrar(
        con,
        tipo="cts",
        ficha_id=componente_id,
        unidade_id=unidade_id,
        autor=autor,
        mudancas=[
            Alteracao(
                campo="macrorregiao",
                antes=None,
                depois="soma de " + ", ".join(m["cts"] for m in membros),
                origem=REGIONAL,
            )
        ],
    )


async def _exigir_caminho_coerente(
    con: Any, *, componente_id: str, sistema_id: str, jusante: str | None
) -> None:
    """As cinco regras de forma. Todas verificadas com a mudança JÁ aplicada em memória."""
    if jusante is None:
        return
    if jusante == componente_id:
        raise TopologiaInvalida(
            f"{componente_id!r} não pode escoar para si mesmo."
        )
    alvo = await con.fetchrow(
        f"""SELECT sistema_id FROM {_i()}.sistema_topologia
             WHERE componente_sistema_id = $1""",
        jusante,
    )
    if alvo is None:
        raise TopologiaInvalida(
            f"{jusante!r} não é um componente do cadastro — não dá para escoar para ele."
        )
    # MESMO sistema, e nao so "algum sistema": a ETE que fecha o caminho e a do
    # sistema (`ete_do_sis[sistema]`), entao um caminho que atravessa a fronteira
    # terminaria numa ETE que nao e a sua. Na base inteira nao ha uma linha assim.
    if alvo["sistema_id"] != sistema_id:
        raise TopologiaInvalida(
            f"{jusante!r} está em outro sistema. O caminho até a ETE não atravessa "
            f"a fronteira do sistema — escolha um componente de {sistema_id!r}."
        )

    # CICLO. O sistema tem poucos componentes, entao o passeio e feito em memoria,
    # com a ligacao nova ja no lugar — e nao numa CTE recursiva, que precisaria da
    # mesma substituicao para valer alguma coisa.
    linhas = await con.fetch(
        f"""SELECT componente_sistema_id AS id, componente_sistema_id_jusante AS jus
              FROM {_i()}.sistema_topologia WHERE sistema_id = $1""",
        sistema_id,
    )
    volta = ciclo_ao_ligar({l["id"]: l["jus"] for l in linhas}, componente_id, jusante)
    if volta:
        raise TopologiaInvalida(
            "Isso fecharia um ciclo: " + " → ".join(volta) + ". O caminho precisa "
            "terminar na ETE, e um ciclo faria o motor repetir o mesmo trecho."
        )


async def salvar_topologia(
    *, unidade_id: str, componente_id: str, corpo: dict[str, Any], autor: str
) -> dict[str, Any]:
    """Coloca o componente num sistema e/ou diz para onde ele escoa.

    O corpo é a posição INTEIRA — `sisId` e `jusante` —, pela mesma razão das
    outras fichas: reenviar o mesmo corpo não acumula efeito. `sisId` vazio tira o
    componente do sistema sem apagar a linha; ver `remover_da_topologia`.
    """
    sistema_id = id_ou_nada(corpo.get("sisId"))
    jusante = id_ou_nada(corpo.get("jusante"))
    if sistema_id is None:
        return await remover_da_topologia(
            unidade_id=unidade_id, componente_id=componente_id, autor=autor
        )

    async with db.transacao() as con:
        # ESPIADA sem lock, só para descobrir QUAIS sistemas travar: mover um
        # componente toca o sistema de origem e o de destino, e o de origem só se
        # sabe lendo. Depois de travar, o valor é lido de novo e conferido — se
        # outra gravação moveu o componente nesse intervalo, esta transação não
        # tem o lock do sistema certo e desiste em vez de decidir no escuro.
        espiada = await con.fetchrow(
            f"""SELECT sistema_id FROM {_i()}.sistema_topologia
                 WHERE componente_sistema_id = $1""",
            componente_id,
        )
        # A UNIDADE PRIMEIRO: ver `_travar_unidade`. A politica de CTS e dela, e
        # so essa ordem impede o abraco com quem esta marcando a caixa.
        await _travar_unidade(con, unidade_id)
        await _travar_sistemas(
            con, sistema_id, espiada["sistema_id"] if espiada else None
        )

        # A UNIDADE USA MACRORREGIÃO? A resposta decide três coisas aqui embaixo,
        # e é a mesma para as três — lê-se uma vez, dentro do lock da unidade que
        # `_travar_unidade` acabou de tomar, e por isso ela não muda no meio.
        usa_macro = await _unidade_usa_cts(con, unidade_id)

        # A MACRORREGIÃO É CRIADA ANTES DE SER PROCURADA. Até este momento ela não
        # tem linha em `cts_operacional`, e `_EXISTE_COMPONENTE` a trataria como id
        # inventado — que é o que ele existe para pegar. Ver `_preparar_macrorregiao`.
        if usa_macro:
            await _preparar_macrorregiao(
                con, unidade_id=unidade_id, componente_id=componente_id, autor=autor
            )
        if not await con.fetchrow(_EXISTE_COMPONENTE.format(i=_i()), componente_id):
            raise FichaDeOutraUnidade(
                f"componente {componente_id!r} nao existe no cadastro"
            )
        # O SISTEMA DE DESTINO e que decide a unidade — e nao a posicao atual do
        # componente. Uma CTS ainda nao colocada nao pertence a unidade nenhuma, e
        # exigir que ela ja fosse desta unidade tornaria impossivel coloca-la.
        if await _unidade_do_sistema(con, sistema_id) != unidade_id:
            raise FichaDeOutraUnidade(
                f"sistema {sistema_id!r} nao pertence a unidade {unidade_id!r}"
            )

        antes = await con.fetchrow(
            f"""SELECT sistema_id, componente_sistema_id_jusante AS jus
                  FROM {_i()}.sistema_topologia WHERE componente_sistema_id = $1""",
            componente_id,
        )
        sis_antigo = antes["sistema_id"] if antes else None
        if sis_antigo != (espiada["sistema_id"] if espiada else None):
            raise TopologiaInvalida(
                f"Outra gravação moveu {componente_id!r} de sistema agora mesmo. "
                "Recarregue a tela e refaça esta alteração."
            )
        # Estava noutra unidade? Recusa. Sem isto, trocar o id da URL movia o
        # componente de uma unidade para outra — e a trilha registrava a de destino
        # como se sempre tivesse sido dela.
        if sis_antigo and sis_antigo != sistema_id:
            if await _unidade_do_sistema(con, sis_antigo) != unidade_id:
                raise FichaDeOutraUnidade(
                    f"componente {componente_id!r} pertence a outra unidade"
                )
            # MUDANCA DE SISTEMA com gente apontando para ele: os que ficam para tras
            # passariam a apontar para fora do sistema deles. Recusar nomeando quem
            # aponta e melhor que religar por conta propria — quem monta o sistema
            # sabe para onde aquele trecho deve escoar; o servidor nao.
            if presos := await _quem_aponta_para(con, componente_id):
                raise TopologiaInvalida(
                    f"{componente_id!r} não pode mudar de sistema enquanto "
                    + ", ".join(repr(p) for p in presos)
                    + " escoa(m) para ele. Reaponte esse(s) primeiro."
                )

        if await _e_ete(con, componente_id):
            # A ETE e o FIM do caminho: na base inteira nao ha uma com jusante.
            if jusante is not None:
                raise TopologiaInvalida(
                    f"{componente_id!r} é a ETE do sistema — ela é o fim do caminho e "
                    "não escoa para lugar nenhum."
                )
            outra = await con.fetchrow(
                f"""SELECT t.componente_sistema_id AS id
                      FROM {_i()}.sistema_topologia t
                      JOIN {_i()}.ete_capex e ON e.ete_id = t.componente_sistema_id
                     WHERE t.sistema_id = $1 AND t.componente_sistema_id <> $2""",
                sistema_id,
                componente_id,
            )
            # O motor guarda UMA ETE por sistema (`ete_do_sis[sis] = comp`): a
            # segunda sobrescreveria a primeira em silencio, e o sistema inteiro
            # passaria a tratar como destino uma estacao que ninguem escolheu.
            if outra:
                raise TopologiaInvalida(
                    f"O sistema {sistema_id!r} já tem a ETE {outra['id']!r}. "
                    "Um sistema tem uma ETE só."
                )

        # UMA CTS POR SISTEMA, quando a UNIDADE usa MACRORREGIAO DE CTS.
        # A regra e do CADASTRO, e nao do motor — para ele uma ou duas CTS sao nos
        # como quaisquer outros. Fica no servidor mesmo assim: a tela ja esconde o
        # seletor, mas quem desmarcar a caixa, adicionar duas e marcar de volta
        # passaria pela tela sem passar por aqui.
        if await _e_cts(con, componente_id) and usa_macro:
            await _exigir_empresa_da_macrorregiao(
                con, componente_id=componente_id, sistema_id=sistema_id
            )
            # MEMBRO NÃO SE COLOCA SOZINHO. A tela marcada oferece macrorregiões, e
            # não os coletores que as formam — colocar um deles é exatamente o que a
            # macrorregião existe para impedir. Chega aqui quem tinha a lista antiga
            # aberta quando a caixa foi marcada, ou quem chama a rota direto.
            dono = await con.fetchval(
                f"""SELECT sistema_cts FROM {_i()}.cts_operacional
                     WHERE cts = $1 AND sistema_cts IS NOT NULL
                       AND btrim(sistema_cts) <> ''""",
                componente_id,
            )
            if dono:
                raise TopologiaInvalida(
                    f"{componente_id!r} faz parte da macrorregião {dono!r}, e a "
                    f"unidade {unidade_id!r} trabalha em macrorregião. Coloque "
                    f"{dono!r} no sistema — ela entra inteira, com os coletores dentro."
                )
            if ja := await _cts_do_sistema(con, sistema_id, exceto=componente_id):
                raise TopologiaInvalida(
                    f"A unidade {unidade_id!r} usa macrorregião de CTS, e o sistema "
                    f"{sistema_id!r} já tem "
                    + ", ".join(repr(c) for c in ja)
                    + ". Desmarque a opção na unidade para ter mais de uma CTS por sistema."
                )

        await _exigir_caminho_coerente(
            con, componente_id=componente_id, sistema_id=sistema_id, jusante=jusante
        )

        mudancas = diferencas(
            {"sisId": sis_antigo, "jusante": antes["jus"] if antes else None},
            {"sisId": sistema_id, "jusante": jusante},
            origem=REGIONAL,
        )
        # `componente_sistema_nome` NAO e tocado: ele vem do Databricks e nao esta
        # no corpo. Um `INSERT` de componente novo o deixa nulo, e a tela cai no id.
        await con.execute(
            f"""INSERT INTO {_i()}.sistema_topologia
                    (componente_sistema_id, sistema_id, componente_sistema_id_jusante)
                VALUES ($1, $2, $3)
                ON CONFLICT (componente_sistema_id) DO UPDATE
                  SET sistema_id                    = EXCLUDED.sistema_id,
                      componente_sistema_id_jusante = EXCLUDED.componente_sistema_id_jusante""",
            componente_id,
            sistema_id,
            jusante,
        )
        n = await _registrar(
            con,
            tipo="topologia",
            ficha_id=componente_id,
            unidade_id=unidade_id,
            autor=autor,
            mudancas=mudancas,
        )
    return {"id": componente_id, "alteracoesGravadas": n}


async def _sistemas_com_varias_cts(con: Any, unidade_id: str) -> dict[str, list[str]]:
    """Os sistemas da unidade que hoje têm mais de uma CTS, e quais são elas.

    É o que impede marcar a unidade: marcada, ela usa macrorregião de CTS e cada
    sistema daqui tem uma CTS só — e um sistema com duas torna a afirmação falsa
    no instante em que ela é gravada.

    Uma consulta para a unidade inteira. Perguntar sistema a sistema seriam 474
    idas ao banco na unidade maior — por um clique numa caixa.
    """
    linhas = await con.fetch(
        f"""WITH cidades AS ({CIDADES_DA_UNIDADE.format(i=_i())})
            SELECT t.sistema_id AS sis, array_agg(t.componente_sistema_id ORDER BY 1) AS cts
              FROM {_i()}.sistema_topologia t
              JOIN {_i()}.cts_operacional o ON o.cts = t.componente_sistema_id
              JOIN {_i()}.cidade_sistema cs ON cs.sistema_id = t.sistema_id
              JOIN cidades c ON c.cidade_id = cs.cidade_id
             GROUP BY t.sistema_id
            HAVING count(*) > 1
             ORDER BY 1""",
        unidade_id,
    )
    return {l["sis"]: list(l["cts"]) for l in linhas}


async def _macrorregioes_colocadas(con: Any, unidade_id: str) -> dict[str, str]:
    """As macrorregiões desta unidade que estão em algum sistema, e onde.

    É o que impede DESMARCAR a unidade. Desmarcada, os coletores membros voltam a
    ser oferecidos e podem ser colocados — enquanto a macrorregião que os SOMA
    continua no sistema. O motor então cria nó para os dois
    (`otimizador_capex_v62.py`, em `ler_banco`: nó nasce de `sistema_topologia`
    para todo componente que exista em `cts_operacional`), e as mesmas ligações,
    a mesma receita e as mesmas obras entram na conta DUAS VEZES.

    Não é estado que a tela denuncie: a macrorregião passa a ser lida como um
    coletor comum, com números que ninguém reconhece como soma de outros quatro.
    """
    linhas = await con.fetch(
        f"""WITH cid AS ({CIDADES_DA_UNIDADE.format(i=_i())})
            SELECT o.cts, t.sistema_id
              FROM {_i()}.cts_operacional o
              JOIN cid ON cid.cidade_id = o.cidade_id
              JOIN {_i()}.sistema_topologia t ON t.componente_sistema_id = o.cts
             WHERE o.e_macrorregiao AND coalesce(t.sistema_id, '') <> ''
             ORDER BY 1""",
        unidade_id,
    )
    return {l["cts"]: l["sistema_id"] for l in linhas}


async def salvar_unidade(
    *, unidade_id: str, corpo: dict[str, Any], autor: str
) -> dict[str, Any]:
    """O que a UNIDADE declara sobre si: se usa macrorregião de CTS, e o WACC médio.

    São os dois campos da unidade que ninguém importa do Databricks — quem os
    informa é gente, e por isso os dois têm de voltar para o banco.

    `usaCts` — MARCADA, a unidade usa MACRORREGIÃO DE CTS: um coletor de tempo
    seco atende a região, e cada sistema dela aceita UMA CTS. Desmarcada, aceitam
    várias. É regra de cadastro: o motor não conta CTS por sistema, e para ele
    elas são nós como quaisquer outros.

    A DECISÃO É DA UNIDADE, e não de cada sistema. Quem opera decide uma vez e
    vale para todos — é a regra de negócio, e é também o que torna a caixa
    respondível: "este sistema usa CTS?" é uma pergunta que quem cadastra não
    tinha como responder 997 vezes.

    Marcar com algum sistema de duas CTS é RECUSADO, e nomeia quais são. A
    alternativa seria aceitar e deixar a unidade num estado que ela própria
    declara impossível — e a recusa da próxima gravação de topologia apareceria
    depois, longe daqui, parecendo defeito.

    DESMARCAR com macrorregião em sistema é RECUSADO pelo espelho da mesma razão,
    e o preço aqui é maior: a macrorregião ficaria no sistema enquanto os
    coletores que ela soma voltariam a ser colocáveis, e a rodada contaria as
    mesmas ligações duas vezes. Tirar a macrorregião do sistema primeiro resolve,
    e não perde o que foi preenchido nela.

    `waccMedio` — o custo médio de capital da unidade, de onde toda obra sem WACC
    próprio herda a taxa de desconto. Vazio vira NULL: no contrato, campo em
    branco é ausência, e zero seria outra coisa (uma unidade que desconta a nada).

    CHAVE AUSENTE NÃO É APAGAMENTO. Só o que vem no corpo é escrito, e é de
    propósito: um `UPDATE` de ficha inteira faria um pedido que só mexe na caixa
    zerar o WACC de quem não mandou o campo — sem erro, e sem ninguém ter tocado
    nele. Corpo sem nenhuma das duas chaves é 422, e não um `UPDATE` vazio.
    """
    tem_cts = "usaCts" in corpo
    tem_wacc = "waccMedio" in corpo
    if not tem_cts and not tem_wacc:
        raise FichaIncompleta("O corpo precisa trazer `usaCts` e/ou `waccMedio`.")

    usa = corpo.get("usaCts")
    if tem_cts and not isinstance(usa, bool):
        raise FichaIncompleta("O corpo precisa trazer `usaCts` como true ou false.")
    wacc = numerico(corpo.get("waccMedio"), "unidade.waccMedio") if tem_wacc else None

    async with db.transacao() as con:
        # O MESMO lock que a topologia toma primeiro, e e isso que serializa as
        # duas: marcar "usa macrorregiao de CTS" e adicionar a segunda CTS sao a
        # mesma regra vista de dois lados, e travando chaves diferentes as duas
        # passavam juntas. Ver `_travar_unidade` para por que o lock e da unidade.
        await _travar_unidade(con, unidade_id)
        antes = await con.fetchrow(
            f"""SELECT usa_macrorregiao_cts, wacc_medio
                  FROM {_i()}.unidade_regional WHERE unidade_id = $1""",
            unidade_id,
        )
        if antes is None:
            raise FichaDeOutraUnidade(f"unidade {unidade_id!r} nao existe")
        # DESMARCAR COM MACRORREGIÃO COLOCADA É RECUSADO — o espelho da regra
        # abaixo, e pela mesma razão: não deixar a unidade num estado que ela
        # própria declara impossível. Aqui o preço é maior que uma tela confusa,
        # porque a rodada passa a contar em duplicidade (ver
        # `_macrorregioes_colocadas`).
        #
        # A saída é explícita e reversível: tirar a macrorregião do sistema — o
        # que devolve os coletores dela à lista — e só então desmarcar. A linha e
        # o que a Regional preencheu nela permanecem.
        if tem_cts and not usa and (postas := await _macrorregioes_colocadas(con, unidade_id)):
            raise TopologiaInvalida(
                f"{len(postas)} macrorregião(ões) da unidade está(ão) em sistema: "
                + "; ".join(f"{m!r} em {sis!r}" for m, sis in postas.items())
                + ". Desmarcar agora deixaria a macrorregião no sistema e liberaria "
                "os coletores que ela soma — a simulação contaria as mesmas ligações "
                "e as mesmas obras duas vezes. Tire-a(s) do sistema primeiro."
            )
        if usa and (cheios := await _sistemas_com_varias_cts(con, unidade_id)):
            raise TopologiaInvalida(
                f"{len(cheios)} sistema(s) da unidade têm mais de uma CTS: "
                + "; ".join(
                    f"{sis!r} tem " + ", ".join(repr(c) for c in cts)
                    for sis, cts in cheios.items()
                )
                + ". Tire as excedentes antes de marcar que a unidade usa macrorregião de CTS."
            )

        # SO AS CHAVES ENVIADAS entram no diff e no UPDATE — ver o docstring.
        de: dict[str, Any] = {}
        para: dict[str, Any] = {}
        colunas: list[str] = []
        valores: list[Any] = []
        if tem_cts:
            de["usaCts"] = bool(antes["usa_macrorregiao_cts"])
            para["usaCts"] = usa
            colunas.append("usa_macrorregiao_cts")
            valores.append(usa)
        if tem_wacc:
            de["waccMedio"] = antes["wacc_medio"]
            para["waccMedio"] = wacc
            colunas.append("wacc_medio")
            valores.append(wacc)

        mudancas = diferencas(de, para, origem=REGIONAL)
        atribui = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(colunas))
        await con.execute(
            f"UPDATE {_i()}.unidade_regional SET {atribui} WHERE unidade_id = $1",
            unidade_id,
            *valores,
        )
        n = await _registrar(
            con,
            tipo="unidade",
            ficha_id=unidade_id,
            unidade_id=unidade_id,
            autor=autor,
            mudancas=mudancas,
        )
    return {"id": unidade_id, "alteracoesGravadas": n}


async def remover_da_topologia(
    *, unidade_id: str, componente_id: str, autor: str
) -> dict[str, Any]:
    """Tira o componente do sistema — a linha FICA, com `sistema_id` nulo.

    Apagar a linha perderia o nome: `componente_sistema_nome` não tem equivalente
    em `cts_operacional`, `subbacia_operacional` nem `ete_capex`, e a lista de CTS
    disponíveis para colocar num sistema viraria uma lista de ids. Com o sistema
    nulo o componente continua cadastrado, some da simulação (o motor pula quem
    não tem sistema) e pode ser colocado em outro sistema depois.
    """
    async with db.transacao() as con:
        # Mesma espiada de `salvar_topologia`: o lock e do SISTEMA de onde ele
        # sai, e qual e so se sabe lendo.
        espiada = await con.fetchrow(
            f"""SELECT sistema_id FROM {_i()}.sistema_topologia
                 WHERE componente_sistema_id = $1""",
            componente_id,
        )
        await _travar_unidade(con, unidade_id)
        await _travar_sistemas(con, espiada["sistema_id"] if espiada else None)
        antes = await con.fetchrow(
            f"""SELECT sistema_id, componente_sistema_id_jusante AS jus
                  FROM {_i()}.sistema_topologia WHERE componente_sistema_id = $1""",
            componente_id,
        )
        if antes is None:
            raise FichaDeOutraUnidade(f"componente {componente_id!r} nao existe")
        if antes["sistema_id"] != (espiada["sistema_id"] if espiada else None):
            raise TopologiaInvalida(
                f"Outra gravação moveu {componente_id!r} de sistema agora mesmo. "
                "Recarregue a tela e refaça esta alteração."
            )
        if antes["sistema_id"] is None:
            # Ja esta fora de qualquer sistema: nada a fazer, e nada na trilha.
            return {"id": componente_id, "alteracoesGravadas": 0}
        if await _unidade_do_sistema(con, antes["sistema_id"]) != unidade_id:
            raise FichaDeOutraUnidade(
                f"componente {componente_id!r} nao pertence a unidade {unidade_id!r}"
            )
        if presos := await _quem_aponta_para(con, componente_id):
            raise TopologiaInvalida(
                f"{componente_id!r} não pode sair do sistema enquanto "
                + ", ".join(repr(p) for p in presos)
                + " escoa(m) para ele. Reaponte esse(s) primeiro."
            )

        mudancas = diferencas(
            {"sisId": antes["sistema_id"], "jusante": antes["jus"]},
            {"sisId": None, "jusante": None},
            origem=REGIONAL,
        )
        await con.execute(
            f"""UPDATE {_i()}.sistema_topologia
                   SET sistema_id = NULL, componente_sistema_id_jusante = NULL
                 WHERE componente_sistema_id = $1""",
            componente_id,
        )
        n = await _registrar(
            con,
            tipo="topologia",
            ficha_id=componente_id,
            # A unidade da trilha e a de ONDE ele saiu: depois da remocao o
            # componente nao tem unidade, e a coluna e NOT NULL com FK.
            unidade_id=unidade_id,
            autor=autor,
            mudancas=mudancas,
        )
    return {"id": componente_id, "alteracoesGravadas": n}

async def _sistema_de_cada(con: Any, ids: list[str]) -> dict[str, str | None]:
    """Onde cada componente está agora. Ausente do resultado = sem linha nenhuma."""
    if not ids:
        return {}
    linhas = await con.fetch(
        f"""SELECT componente_sistema_id AS id, sistema_id AS sis
              FROM {_i()}.sistema_topologia
             WHERE componente_sistema_id = ANY($1::text[])""",
        ids,
    )
    return {l["id"]: l["sis"] for l in linhas}


async def _quais_sao(con: Any, tabela: str, coluna: str, ids: list[str]) -> set[str]:
    """Quais destes componentes são ETE (ou CTS). Conjunto, para o teste ser `in`."""
    if not ids:
        return set()
    linhas = await con.fetch(
        f"SELECT {coluna} AS id FROM {_i()}.{tabela} WHERE {coluna} = ANY($1::text[])",
        ids,
    )
    return {l["id"] for l in linhas}


async def _unidades_dos_sistemas(con: Any, ids: list[str]) -> dict[str, str]:
    """De que unidade é cada um destes sistemas. Ausente = sistema inexistente.

    PLURAL de propósito. A versão de um id por vez, chamada num laço, é uma ida
    ao banco por sistema dentro de uma transação que já segura os advisory locks
    de todos eles — e é o padrão N+1 que a régua de Postgres manda eliminar.
    Segurar lock enquanto se conversa com o banco N vezes é o mesmo defeito visto
    do lado do bloqueio.
    """
    if not ids:
        return {}
    linhas = await con.fetch(
        f"""SELECT cs.sistema_id AS sis, s.unidade_id AS uni
              FROM {_i()}.cidade_sistema cs
              JOIN {_i()}.cidade_empresa c ON c.cidade_id = cs.cidade_id
              JOIN {_i()}.empresa s USING (emp_codigo)
             WHERE cs.sistema_id = ANY($1::text[])""",
        ids,
    )
    return {l["sis"]: l["uni"] for l in linhas}


async def _quem_aponta_para_varios(con: Any, ids: list[str]) -> dict[str, list[str]]:
    """Para cada componente, quem escoa para ele. Uma consulta, não uma por alvo.

    `ix_topo_jusante` cobre o filtro; a agregação é em memória porque o resultado
    é pequeno (no máximo um punhado de montantes por componente).
    """
    if not ids:
        return {}
    linhas = await con.fetch(
        f"""SELECT componente_sistema_id AS id, componente_sistema_id_jusante AS jus
              FROM {_i()}.sistema_topologia
             WHERE componente_sistema_id_jusante = ANY($1::text[])
             ORDER BY 1""",
        ids,
    )
    saida: dict[str, list[str]] = {}
    for l in linhas:
        saida.setdefault(l["jus"], []).append(l["id"])
    return saida


async def _quais_existem(con: Any, ids: list[str]) -> set[str]:
    """Quais destes componentes existem em alguma ficha do cadastro."""
    if not ids:
        return set()
    linhas = await con.fetch(
        f"""SELECT id FROM (
                SELECT componente_sistema_id AS id FROM {_i()}.sistema_topologia
                UNION SELECT sub_bacia FROM {_i()}.subbacia_operacional
                UNION SELECT cts       FROM {_i()}.cts_operacional
                UNION SELECT ete_id    FROM {_i()}.ete_capex
            ) t WHERE id = ANY($1::text[])""",
        ids,
    )
    return {l["id"] for l in linhas}


async def _registrar_varios(
    con: Any,
    *,
    tipo: str,
    unidade_id: str,
    autor: str,
    por_ficha: list[tuple[str, list[Alteracao]]],
) -> int:
    """A trilha de VÁRIAS fichas num `executemany` só.

    Mesma regra de `_registrar` — quem compara é o servidor, o autor vem do
    token, e nada aqui apaga nada. A diferença é só o número de idas ao banco:
    gravar o desenho de dez sistemas fazia dez `executemany`, um por componente
    alterado, dentro da mesma transação.
    """
    linhas = [
        (tipo, ficha_id, unidade_id, m.campo, m.antes, m.depois, autor, m.origem)
        for ficha_id, mudancas in por_ficha
        for m in mudancas
    ]
    if not linhas:
        return 0
    await con.executemany(
        f"""INSERT INTO {_i()}.override
                (tipo, ficha_id, unidade_id, campo, valor_antigo, valor_novo,
                 autor, origem)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        linhas,
    )
    return len(linhas)


async def salvar_topologia_em_lote(
    *, unidade_id: str, corpo: dict[str, Any], autor: str
) -> dict[str, Any]:
    """Grava o desenho de um ou mais sistemas INTEIROS, numa transação só.

    Existe porque gravar componente a componente cobra do cliente uma ORDEM que
    nem sempre existe. As regras de `salvar_topologia` são conferidas contra o
    banco, então cada passo intermediário precisa estar de pé — e reorganizar um
    sistema passa por estados que não estão:

        tirar a CTS 'b' do sistema e reapontar 'a', que escoava para ela
          tirar 'b' primeiro  → recusado, 'a' ainda escoa para 'b'
          reapontar 'a' antes → o cliente teria de saber disso; o front ordenava
                                pelo estado final da própria linha e mandava a
                                saída de 'b' na frente. Era este o defeito.

        mover a cadeia 'a → b' de um sistema para outro
          mover 'b' → recusado, 'a' aponta para ele
          mover 'a' → recusado, o jusante 'b' está noutro sistema
          NENHUMA ordem funciona: o estado intermediário é que é impossível.

    Aqui o estado intermediário não existe. A conferência é sobre o desenho que
    chegou (`problemas_do_sistema`), e o banco só vê o antes e o depois.

    NADA foi afrouxado: as regras são as mesmas, e as que dependem do mundo fora
    dos sistemas enviados continuam valendo contra o banco — quem escoa para um
    componente que está saindo, se não vier neste envio, ainda barra a gravação.
    A saída, nesse caso, é mandar o sistema dele junto.

    A trilha continua por COMPONENTE (`tipo="topologia"`, `ficha_id` = componente),
    idêntica à das rotas de uma ficha: o lote é uma forma de gravar, e não uma
    unidade de auditoria. Quem só olha `GET /alteracoes` não vê diferença.
    """
    pedido = pedido_do_corpo(corpo)
    enviados = sorted({c for mapa in pedido.values() for c in mapa})

    async with db.transacao() as con:
        # ESPIADA sem lock, pela mesma razao de `salvar_topologia`: trazer um
        # componente de outro sistema toca os DOIS, e o de origem so se sabe lendo.
        # Os sistemas do corpo ja sao conhecidos e entram na trava direto.
        espiada = await _sistema_de_cada(con, enviados)
        await _travar_unidade(con, unidade_id)
        await _travar_sistemas(con, *pedido, *espiada.values())
        if await _sistema_de_cada(con, enviados) != espiada:
            raise TopologiaInvalida(
                "Outra gravação moveu componentes de sistema agora mesmo. "
                "Recarregue a tela e refaça esta alteração."
            )

        # O ANTES completo: os componentes enviados MAIS os que hoje moram nos
        # sistemas do corpo. Estes ultimos e que revelam quem esta saindo — sem
        # eles, ausencia na lista nao teria como virar remocao.
        linhas = await con.fetch(
            f"""SELECT componente_sistema_id AS id, sistema_id AS sis,
                       componente_sistema_id_jusante AS jus
                  FROM {_i()}.sistema_topologia
                 WHERE sistema_id = ANY($1::text[])
                    OR componente_sistema_id = ANY($2::text[])""",
            list(pedido),
            enviados,
        )
        antes: dict[str, tuple[str | None, str | None]] = {
            l["id"]: (l["sis"], l["jus"]) for l in linhas
        }

        # O MESMO CAMINHO DA MACRORREGIÃO, e aqui ele importa mais: montar o
        # sistema inteiro de uma vez é o que a tela faz. Sem isto, a macrorregião
        # passaria pela rota de um componente só e seria recusada nesta —
        # `_quais_existem` a trataria como id inventado, que é o que ele existe
        # para pegar. Ver `_preparar_macrorregiao`.
        usa_macro = await _unidade_usa_cts(con, unidade_id)
        if usa_macro:
            macros = await _nomes_de_macrorregiao(con, unidade_id)
            for componente_id in enviados:
                if componente_id in antes or componente_id not in macros:
                    continue
                await _preparar_macrorregiao(
                    con,
                    unidade_id=unidade_id,
                    componente_id=componente_id,
                    autor=autor,
                )

        # Quem NAO tem linha na topologia precisa existir em alguma ficha. Uma
        # consulta para todos, e nao uma por componente: ver `_quais_existem`.
        sem_linha = [c for c in enviados if c not in antes]
        if faltam := sorted(set(sem_linha) - await _quais_existem(con, sem_linha)):
            raise FichaDeOutraUnidade(
                f"componente {faltam[0]!r} nao existe no cadastro"
            )

        # A UNIDADE dos sistemas do corpo E dos sistemas de ORIGEM, de uma vez.
        # Trazer componente de um sistema que nao veio no corpo exige que o de
        # origem tambem seja desta unidade: sem isto, trocar o id da URL movia
        # componente de uma unidade para outra, e a trilha registrava a de
        # destino como se sempre tivesse sido dela.
        origens = {
            antes.get(c, (None, None))[0]
            for c in enviados
            if antes.get(c, (None, None))[0] and antes.get(c, (None, None))[0] not in pedido
        }
        unidades = await _unidades_dos_sistemas(con, sorted(set(pedido) | origens))
        for sistema_id in pedido:
            if unidades.get(sistema_id) != unidade_id:
                raise FichaDeOutraUnidade(
                    f"sistema {sistema_id!r} nao pertence a unidade {unidade_id!r}"
                )
        for componente_id in enviados:
            origem = antes.get(componente_id, (None, None))[0]
            if origem and origem not in pedido and unidades.get(origem) != unidade_id:
                raise FichaDeOutraUnidade(
                    f"componente {componente_id!r} pertence a outra unidade"
                )

        depois: dict[str, tuple[str | None, str | None]] = {
            componente_id: (sistema_id, jusante)
            for sistema_id, mapa in pedido.items()
            for componente_id, jusante in mapa.items()
        }
        # AUSENCIA E REMOCAO: quem mora num sistema do corpo e nao foi listado sai
        # dele. Fica sem sistema, e a linha PERMANECE — apagar perderia
        # `componente_sistema_nome`, que nao tem equivalente nas fichas.
        for componente_id, (sistema_id, _jus) in antes.items():
            if sistema_id in pedido and componente_id not in depois:
                depois[componente_id] = (None, None)

        # QUEM E ETE e QUEM E CTS: duas consultas no total. Eram duas POR SISTEMA
        # — e `_quais_sao` ja aceitava lista, entao o laco pagava N vezes por uma
        # consulta que sempre soube responder de uma vez. Os conjuntos sao de
        # todos os componentes enviados; a regra de cada sistema olha so a
        # fronteira dele (`set(escoa)`).
        #
        # A TERCEIRA consulta sumiu junto com a coluna do sistema: "quais destes
        # sistemas sao de CTS" virou uma pergunta so, feita a unidade.
        etes = await _quais_sao(con, "ete_capex", "ete_id", enviados)
        ctss = await _quais_sao(con, "cts_operacional", "cts", enviados)

        # MEMBRO NÃO SE COLOCA SOZINHO — a mesma regra de `salvar_topologia`, na
        # rota que grava o sistema inteiro. Uma consulta para todos os enviados:
        # a lista de um sistema grande chega com dezenas de componentes.
        if usa_macro:
            membros = await con.fetch(
                f"""SELECT cts, sistema_cts FROM {_i()}.cts_operacional
                     WHERE cts = ANY($1::text[]) AND sistema_cts IS NOT NULL
                       AND btrim(sistema_cts) <> ''
                     ORDER BY 1""",
                enviados,
            )
            if membros:
                raise TopologiaInvalida(
                    "; ".join(
                        f"{l['cts']!r} faz parte da macrorregião {l['sistema_cts']!r}"
                        for l in membros
                    )
                    + f". A unidade {unidade_id!r} trabalha em macrorregião: coloque "
                    "a macrorregião no sistema — ela entra inteira, com os "
                    "coletores dentro."
                )
            # E A MACRORREGIÃO SÓ NO SISTEMA DA EMPRESA DELA — a mesma regra da
            # rota de um componente, para cada macrorregião do envio.
            for sistema_id, mapa in pedido.items():
                for componente_id in mapa:
                    if componente_id in ctss:
                        await _exigir_empresa_da_macrorregiao(
                            con, componente_id=componente_id, sistema_id=sistema_id
                        )

        problemas: list[str] = []
        for sistema_id, mapa in pedido.items():
            problemas += problemas_do_sistema(
                mapa,
                sistema_id=sistema_id,
                etes=etes,
                ctss=ctss,
                usa_cts=usa_macro,
            )
        if problemas:
            raise TopologiaInvalida(" ".join(problemas))

        # QUEM FICA PENDURADO FORA DO ENVIO. Dentro dos sistemas enviados isto ja
        # foi conferido — `problemas_do_sistema` recusa jusante fora da fronteira.
        # Aqui sobra o mundo de fora: um componente que sai leva junto quem escoava
        # para ele, e reapontar por conta propria nao e papel do servidor.
        saindo = [
            c
            for c, (sistema_novo, _jus) in sorted(depois.items())
            if antes.get(c, (None, None))[0] is not None
            and sistema_novo != antes.get(c, (None, None))[0]
        ]
        montantes = await _quem_aponta_para_varios(con, saindo)
        for componente_id in saindo:
            sistema_velho = antes[componente_id][0]
            presos = [p for p in montantes.get(componente_id, []) if p not in depois]
            if presos:
                raise TopologiaInvalida(
                    f"{componente_id!r} não pode sair do sistema {sistema_velho!r} "
                    "enquanto "
                    + ", ".join(repr(p) for p in presos)
                    + " escoa(m) para ele. Reaponte esse(s) primeiro, ou mande o "
                    f"sistema {sistema_velho!r} neste mesmo envio para as duas "
                    "mudanças valerem juntas."
                )

        # A GRAVACAO EM TRES COMANDOS, e nao dois por componente alterado.
        # Uma transacao que segura os advisory locks de N sistemas nao deve
        # gastar 2N idas ao banco enquanto os segura — e o `executemany` diz a
        # mesma coisa em uma.
        tiram_do_sistema: list[tuple[str]] = []
        entram: list[tuple[str, str, str | None]] = []
        trilha: list[tuple[str, list[Alteracao]]] = []
        for componente_id in sorted(depois):
            sistema_novo, jusante = depois[componente_id]
            sistema_velho, jusante_velho = antes.get(componente_id, (None, None))
            if (sistema_novo, jusante) == (sistema_velho, jusante_velho):
                continue
            if sistema_novo is None:
                tiram_do_sistema.append((componente_id,))
            else:
                entram.append((componente_id, sistema_novo, jusante))
            trilha.append(
                (
                    componente_id,
                    diferencas(
                        {"sisId": sistema_velho, "jusante": jusante_velho},
                        {"sisId": sistema_novo, "jusante": jusante},
                        origem=REGIONAL,
                    ),
                )
            )

        if tiram_do_sistema:
            # A linha PERMANECE, com o sistema nulo: apagar perderia
            # `componente_sistema_nome`, que nao tem equivalente nas fichas.
            await con.executemany(
                f"""UPDATE {_i()}.sistema_topologia
                       SET sistema_id = NULL, componente_sistema_id_jusante = NULL
                     WHERE componente_sistema_id = $1""",
                tiram_do_sistema,
            )
        if entram:
            # `componente_sistema_nome` NAO e tocado: vem do Databricks e nao
            # esta no corpo.
            await con.executemany(
                f"""INSERT INTO {_i()}.sistema_topologia
                        (componente_sistema_id, sistema_id,
                         componente_sistema_id_jusante)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (componente_sistema_id) DO UPDATE
                      SET sistema_id                    = EXCLUDED.sistema_id,
                          componente_sistema_id_jusante = EXCLUDED.componente_sistema_id_jusante""",
                entram,
            )
        # A unidade da trilha e a do envio: quem sai de sistema fica sem
        # unidade, e a coluna e NOT NULL com FK.
        gravadas = await _registrar_varios(
            con, tipo="topologia", unidade_id=unidade_id, autor=autor, por_ficha=trilha
        )
    return {"id": unidade_id, "alteracoesGravadas": gravadas}
