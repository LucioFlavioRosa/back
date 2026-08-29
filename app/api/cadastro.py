"""Cadastro da unidade — o que alimenta a simulação.  `DEPLOY.md` §3 do front.

São dois lados com naturezas diferentes:

    LEITURA   8 endpoints, um por grupo de fichas.
    ESCRITA   7 endpoints. Em seis o corpo é UMA ficha inteira — não um patch —, e
              a trilha de override viaja junto na mesma transação. O sétimo é a
              topologia em lote: o desenho de um ou mais sistemas inteiros, também
              numa transação, porque validar o caminho passo a passo cobrava do
              cliente uma ordem de envio que nem sempre existe.

A ficha de coleta (sub-bacia e CTS, que são iguais) tem dois blocos de origem
diferente, e isso atravessa todo o cadastro:

  `db`      vem do Databricks, é travado na tela e corrigível só por override
  `params`  a Regional preenche

`params` viaja sempre inteiro, inclusive `popU`/`popA`. A régua de cobertura da
cidade decide se esses dois **aparecem** e se contam pendência — não se são
enviados. É de propósito: trocar a régua de uma cidade não pode apagar o que
alguém já preencheu.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.api.deps import Quem, Usuario, guarda_de_rota
from app.api import formas_cadastro as formas
from app.infra.repositorios import cadastro, cadastro_escrita

#: PREFIXO E GUARDA MORAM NO ROTEADOR, e nao no `include_router`. Assim quem
#: le este arquivo ve sob que caminho as rotas abaixo vivem e que elas ja
#: nascem protegidas — sem precisar abrir o `main.py` para descobrir.
#:
#: `guarda_de_rota` le `{unidade_id}`/`{run_id}` do caminho; `main.py` nao
#: perde a visao do conjunto porque `test_guarda_de_rota.py` cobra que TODO
#: roteador servido sob /api traga esta dependencia.
router = APIRouter(
    prefix="/api",
    tags=["cadastro"],
    dependencies=[Depends(guarda_de_rota)],
)


async def _ou_404(valor, o_que: str, feminino: bool = False):
    """404 com o texto concordando — "Unidade não encontrado" apareceu num teste
    de uso real, e mensagem de erro malescrita corrói a confiança no resto."""
    if valor is None:
        achado = "encontrada" if feminino else "encontrado"
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{o_que} não {achado}.")
    return valor


# ---------------------------------------------------------------- organização
#
# Estas duas rotas NAO tem `{unidade_id}` no caminho, entao a `guarda_de_rota`
# nao as alcanca — e sem o recorte explicito abaixo qualquer login, inclusive um
# SEM concessao nenhuma, enumerava todas as regionais, todas as unidades, e os
# resumos operacionais delas. E o vazamento mais barato que existe: entrega o mapa
# da organizacao inteira numa requisicao, antes de qualquer tentativa de acesso.
@router.get("/regionais", response_model=list[formas.Regional])
async def regionais(quem: Quem) -> list[dict[str, Any]]:
    todas = await cadastro.regionais()
    if quem.tudo:
        return todas
    # Uma regional aparece quando o usuario acessa ALGUMA unidade dela — inclusive
    # quando a concessao e por unidade solta, e nao pela regional inteira.
    minhas = {
        r["id"]
        for r in todas
        if any(u["id"] in quem.unidades for u in await cadastro.unidades(r["id"]))
    }
    return [r for r in todas if r["id"] in minhas]


@router.get("/regionais/{regional_id}/unidades", response_model=list[formas.Unidade])
async def unidades(regional_id: str, quem: Quem) -> list[dict[str, Any]]:
    todas = await cadastro.unidades(regional_id)
    if quem.tudo:
        return todas
    # Lista vazia, e nao 404: a regional pode existir e o usuario ter acesso a
    # nenhuma unidade dela. Uma lista vazia nao afirma nem nega a existencia da
    # regional, que e o que se quer.
    return [u for u in todas if u["id"] in quem.unidades]


@router.get("/unidades/{unidade_id}", response_model=formas.Unidade)
async def unidade(unidade_id: str) -> dict[str, Any]:
    return await _ou_404(await cadastro.unidade(unidade_id), "Unidade", feminino=True)


# ------------------------------------------------------------------- fichas
@router.get("/unidades/{unidade_id}/hierarquia", response_model=formas.Hierarquia)
async def hierarquia(unidade_id: str) -> dict[str, Any]:
    """Grupo 01 — a árvore organizacional inteira, do Databricks.

    Cinco níveis numa resposta só porque a tela desenha a árvore completa: buscar
    por nível faria a tela montar em cascata, com um salto visual a cada nível.
    """
    return await cadastro.hierarquia(unidade_id)


@router.get("/unidades/{unidade_id}/contrato", response_model=formas.Contrato)
async def contrato(unidade_id: str) -> dict[str, Any]:
    """Grupo 02 — cidades, metas de cobertura e faixas de paridade.

    `fator` é a tabela cobertura → fator de esgoto. É a mesma que a tela de
    resultado precisa para explicar o degrau de paridade e hoje não recebe
    (ver o README): aqui ela existe, porque é cadastro.
    """
    return await cadastro.contrato(unidade_id)


@router.get("/unidades/{unidade_id}/sub-bacias", response_model=formas.SubBacias)
async def sub_bacias(unidade_id: str) -> dict[str, Any]:
    """Grupo 03 — a árvore de coleta e as fichas.

    `arvore` é o rail de navegação (cidade → sistema → sub-bacia); `subs` são as
    fichas. Separados porque o rail fica montado enquanto o usuário troca de ficha.
    """
    return await cadastro.sub_bacias(unidade_id)


@router.get("/unidades/{unidade_id}/etes", response_model=formas.Etes)
async def etes(unidade_id: str) -> dict[str, Any]:
    return await cadastro.etes(unidade_id)


@router.get("/unidades/{unidade_id}/cts", response_model=formas.Cts)
async def cts(unidade_id: str) -> dict[str, Any]:
    """Grupo 05 — CTS e o pareamento 1:1 com a sub-bacia.

    `pares` existe separado de `ctss` porque uma CTS **sem** par é estado inválido
    que a tela precisa mostrar (e foi bug real do outro lado): sem a lista de
    pares, a tela não teria como saber que a CTS ficou órfã.
    """
    return await cadastro.cts(unidade_id)


@router.get("/unidades/{unidade_id}/alteracoes", response_model=formas.Alteracoes)
async def alteracoes(
    unidade_id: str,
    tipo: Annotated[str | None, Query()] = None,
    fichaId: Annotated[str | None, Query()] = None,  # noqa: N803 — camelCase e a convencao do contrato
    limite: Annotated[int, Query()] = cadastro.LIMITE_ALTERACOES,
) -> dict[str, Any]:
    """A trilha de auditoria do cadastro: quem mudou o quê, quando.

    Sem filtro, e o que mudou na UNIDADE — a pergunta de quem audita. Com
    `tipo` e `fichaId`, e o historico de UMA ficha, que e o que a tela abre a
    partir da linha "ultima alteracao".

    Esta rota e nova, e a ausencia dela era o defeito: a trilha existia desde a
    migracao 001, crescia a cada gravacao, e nao havia como le-la pelo produto.
    Auditoria que so o DBA alcanca nao e auditoria — alguem ia confiar nela numa
    discussao sobre um numero e descobrir que ninguem conseguia abrir.

    `GET` e nao parte da ficha: o historico e volumoso, muda por outro motivo que
    a ficha, e ninguem quer paga-lo em toda abertura de tela. Quem quiser so o
    ultimo evento ja o tem em `atualizadoEm`/`atualizadoPor`, dentro da ficha.
    """
    return await cadastro.alteracoes(
        unidade_id,
        tipo=tipo,
        ficha_id=fichaId,
        limite=max(1, min(limite, cadastro.LIMITE_ALTERACOES)),
    )


# ---------------------------------------------------------------------------
# ESCRITA — uma ficha por vez, e o corpo e a ficha inteira
# ---------------------------------------------------------------------------
# O `autor` sai do TOKEN, nunca do corpo. Ele assina a trilha, e aceita-lo do
# cliente seria aceitar que alguem assinasse a correcao de outro — numa trilha de
# auditoria isso e o defeito que a anula inteira.
#
# O CORPO NAO CARREGA A TRILHA. Quem a calcula e o SERVIDOR, comparando o que
# esta gravado com o que chegou (`cadastro_escrita.diferencas`) — auditoria que
# pergunta ao auditado o que ele mudou tem o defeito no desenho. A trilha cobre a
# ficha inteira: `params`, `db`, obras, cidade, metas, faixas e ETE.
#
# A resposta traz `alteracoesGravadas` de proposito: e o unico jeito de quem
# chamou conferir que a trilha foi junto, sem consultar o banco.
#
# E TODA escrita passa por `exigir_dona`: o `unidade_id` do caminho recorta a
# ficha, e nao so assina a trilha. Sem isso dava para gravar na sub-bacia de
# outra unidade trocando o id da URL — e a auditoria registrava a unidade errada.

Corpo = Annotated[dict[str, Any], Body()]


@router.put("/unidades/{unidade_id}/sub-bacias/{sub_id}", response_model=formas.Gravacao, response_model_exclude_unset=True)
async def salvar_sub_bacia(
    unidade_id: str, sub_id: str, corpo: Corpo, usuario: Usuario
) -> dict[str, Any]:
    return await cadastro_escrita.salvar_coleta(
        unidade_id=unidade_id, ficha_id=sub_id, corpo=corpo, autor=usuario, e_cts=False
    )


@router.put("/unidades/{unidade_id}/cts/{cts_id}", response_model=formas.Gravacao, response_model_exclude_unset=True)
async def salvar_cts(
    unidade_id: str, cts_id: str, corpo: Corpo, usuario: Usuario
) -> dict[str, Any]:
    return await cadastro_escrita.salvar_coleta(
        unidade_id=unidade_id, ficha_id=cts_id, corpo=corpo, autor=usuario, e_cts=True
    )


@router.put("/unidades/{unidade_id}/contrato/{cidade_id}", response_model=formas.Gravacao, response_model_exclude_unset=True)
async def salvar_contrato(
    unidade_id: str, cidade_id: str, corpo: Corpo, usuario: Usuario
) -> dict[str, Any]:
    return await cadastro_escrita.salvar_contrato(
        unidade_id=unidade_id, cidade_id=cidade_id, corpo=corpo, autor=usuario
    )


@router.put("/unidades/{unidade_id}/etes/{ete_id}", response_model=formas.Gravacao, response_model_exclude_unset=True)
async def salvar_ete(
    unidade_id: str, ete_id: str, corpo: Corpo, usuario: Usuario
) -> dict[str, Any]:
    return await cadastro_escrita.salvar_ete(
        unidade_id=unidade_id, ete_id=ete_id, corpo=corpo, autor=usuario
    )


@router.put("/unidades/{unidade_id}/topologia/{componente_id}", response_model=formas.Gravacao, response_model_exclude_unset=True)
async def salvar_topologia(
    unidade_id: str, componente_id: str, corpo: Corpo, usuario: Usuario
) -> dict[str, Any]:
    """Grupo 01 — em que sistema o componente entra, e para onde ele escoa.

    O corpo é a posição inteira: `{"sisId": "d1s25", "jusante": "d1b25_1_1"}`.
    `jusante` vazio é caminho ainda não montado; `sisId` vazio tira o componente
    do sistema, o mesmo que o `DELETE` abaixo.

    **Não há `atualizadoEm`/`atualizadoPor` na resposta**, ao contrário das outras
    quatro rotas: `sistema_topologia` não tem essas colunas. Quem gravou o quê está
    na trilha (`GET /alteracoes?tipo=topologia`), que é onde a tela vai buscar.
    """
    return await cadastro_escrita.salvar_topologia(
        unidade_id=unidade_id, componente_id=componente_id, corpo=corpo, autor=usuario
    )


@router.put("/unidades/{unidade_id}/topologia", response_model=formas.Gravacao, response_model_exclude_unset=True)
async def salvar_topologia_em_lote(
    unidade_id: str, corpo: Corpo, usuario: Usuario
) -> dict[str, Any]:
    """Grupo 01 — o desenho de um ou mais sistemas INTEIROS, numa transação só.

    ```json
    {"sistemas": [
        {"id": "d1s25", "componentes": [
            {"id": "d1b25_1_1", "jusante": "d1e25"},
            {"id": "d1e25",     "jusante": ""}
        ]}
    ]}
    ```

    `componentes` é a lista **completa** do sistema, e não as linhas que mudaram:
    quem está no sistema hoje e não vem na lista **sai** dele — é assim que tirar
    uma CTS se expressa aqui. Lista vazia esvazia o sistema.

    **Prefira esta rota à de um componente por vez** quando o cliente tem o desenho
    pronto na tela. A rota `PUT .../topologia/{componente_id}` valida cada mudança
    contra o que está gravado, e por isso cobra uma ordem de envio que às vezes não
    existe: tirar uma CTS e reapontar quem escoava para ela é recusado nas duas
    ordens possíveis. Aqui a conferência é sobre o desenho final.

    As regras são as MESMAS (sem ciclo, jusante dentro do sistema, uma ETE, a regra
    de CTS) e a recusa continua sendo 422 com o motivo inteiro — agora com **todos**
    os problemas de uma vez, e não o primeiro. As rotas de um componente por vez
    continuam existindo e não mudaram.

    Como a de um componente, **não devolve `atualizadoEm`/`atualizadoPor`**:
    `sistema_topologia` não tem essas colunas. `alteracoesGravadas` soma as
    diferenças de todos os componentes tocados, e a trilha continua por componente.
    """
    return await cadastro_escrita.salvar_topologia_em_lote(
        unidade_id=unidade_id, corpo=corpo, autor=usuario
    )


@router.put("/unidades/{unidade_id}/sistemas/{sistema_id}", response_model=formas.Gravacao, response_model_exclude_unset=True)
async def salvar_sistema(
    unidade_id: str, sistema_id: str, corpo: Corpo, usuario: Usuario
) -> dict[str, Any]:
    """Grupo 01 — o que o sistema declara sobre si: `{"usaCts": true|false}`.

    Marcado, o sistema aceita **uma** CTS; desmarcado, aceita várias. É regra de
    cadastro, e não de simulação: o motor nunca contou CTS por sistema.

    O nome do sistema NÃO entra aqui — ele vem do Databricks e não tem rota de
    escrita, como o resto dos nomes do Grupo 01.
    """
    return await cadastro_escrita.salvar_sistema(
        unidade_id=unidade_id, sistema_id=sistema_id, corpo=corpo, autor=usuario
    )


@router.delete("/unidades/{unidade_id}/topologia/{componente_id}", response_model=formas.Gravacao, response_model_exclude_unset=True)
async def remover_da_topologia(
    unidade_id: str, componente_id: str, usuario: Usuario
) -> dict[str, Any]:
    """Tira o componente do sistema. A ficha dele CONTINUA no cadastro.

    Não é apagar: a linha fica com `sistema_id` nulo, e é isso que preserva o nome
    do componente e permite colocá-lo noutro sistema depois. Fora de sistema, ele
    some da simulação — o motor pula quem não tem sistema.
    """
    return await cadastro_escrita.remover_da_topologia(
        unidade_id=unidade_id, componente_id=componente_id, autor=usuario
    )


# ---------------------------------------------------------------------------
# COLOCAR e TIRAR CTS: pela TOPOLOGIA, e so por ela
# ---------------------------------------------------------------------------
# A CTS e um NO DO SISTEMA, como a sub-bacia. Onde ela esta e uma linha de
# `input.sistema_topologia`, e e por isso que colocar e tirar CTS sao as duas
# rotas de topologia acima, e nao um `POST`/`DELETE` de ficha.
#
# O motor deixa isso obrigatorio (`otimizador_capex_v62.py`): os nos saem do laco
# sobre `sistema-topologia`, e `cen.cts_ids = set(cts_operacional) & set(cen.nos)`.
# So e CTS efetiva a ficha que TAMBEM e no. Mexer num lado sem o outro produz meia
# CTS, das duas formas possiveis — e as duas ja aconteceram aqui:
#
#   - ficha sem no: visivel no cadastro, invisivel para a simulacao. Houve um
#     periodo com 339 fichas para 337 nos, e as duas sobrando eram exatamente isso.
#   - no sem ficha: pior. Vira um no comum, sem componentes e com demanda ZERADA,
#     que continua no caminho ate a ETE sem aparecer em tela nenhuma.
#
# Por isso `salvar_topologia` exige que o componente ja exista em alguma ficha, e
# `remover_da_topologia` NAO apaga a linha: ela fica com `sistema_id` nulo. As duas
# metades permanecem presas uma na outra.
#
# `subbacia_cts` e SOBREPOSICAO de area, e nao pertencimento — nem a sistema, nem a
# unidade. Ela nao diz onde a CTS esta.
