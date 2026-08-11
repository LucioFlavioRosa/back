"""Cadastro da unidade — o que alimenta a simulação.  `DEPLOY.md` §3 do front.

São dois lados com naturezas diferentes:

    LEITURA   8 endpoints, um por grupo de fichas.
    ESCRITA   6 endpoints, uma ficha por vez — o corpo é a ficha inteira, não um
              patch, e a trilha de override viaja junto na mesma transação.

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

from fastapi import APIRouter, Body, HTTPException, status

from app.api.deps import Quem, Usuario
from app.infra.repositorios import cadastro, cadastro_escrita

router = APIRouter(tags=["cadastro"])


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
@router.get("/regionais")
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


@router.get("/regionais/{regional_id}/unidades")
async def unidades(regional_id: str, quem: Quem) -> list[dict[str, Any]]:
    todas = await cadastro.unidades(regional_id)
    if quem.tudo:
        return todas
    # Lista vazia, e nao 404: a regional pode existir e o usuario ter acesso a
    # nenhuma unidade dela. Uma lista vazia nao afirma nem nega a existencia da
    # regional, que e o que se quer.
    return [u for u in todas if u["id"] in quem.unidades]


@router.get("/unidades/{unidade_id}")
async def unidade(unidade_id: str) -> dict[str, Any]:
    return await _ou_404(await cadastro.unidade(unidade_id), "Unidade", feminino=True)


# ------------------------------------------------------------------- fichas
@router.get("/unidades/{unidade_id}/hierarquia")
async def hierarquia(unidade_id: str) -> dict[str, Any]:
    """Grupo 01 — a árvore organizacional inteira, do Databricks.

    Cinco níveis numa resposta só porque a tela desenha a árvore completa: buscar
    por nível faria a tela montar em cascata, com um salto visual a cada nível.
    """
    return await cadastro.hierarquia(unidade_id)


@router.get("/unidades/{unidade_id}/contrato")
async def contrato(unidade_id: str) -> dict[str, Any]:
    """Grupo 02 — cidades, metas de cobertura e faixas de paridade.

    `fator` é a tabela cobertura → fator de esgoto. É a mesma que a tela de
    resultado precisa para explicar o degrau de paridade e hoje não recebe
    (ver o README): aqui ela existe, porque é cadastro.
    """
    return await cadastro.contrato(unidade_id)


@router.get("/unidades/{unidade_id}/sub-bacias")
async def sub_bacias(unidade_id: str) -> dict[str, Any]:
    """Grupo 03 — a árvore de coleta e as fichas.

    `arvore` é o rail de navegação (cidade → sistema → sub-bacia); `subs` são as
    fichas. Separados porque o rail fica montado enquanto o usuário troca de ficha.
    """
    return await cadastro.sub_bacias(unidade_id)


@router.get("/unidades/{unidade_id}/etes")
async def etes(unidade_id: str) -> dict[str, Any]:
    return await cadastro.etes(unidade_id)


@router.get("/unidades/{unidade_id}/cts")
async def cts(unidade_id: str) -> dict[str, Any]:
    """Grupo 05 — CTS e o pareamento 1:1 com a sub-bacia.

    `pares` existe separado de `ctss` porque uma CTS **sem** par é estado inválido
    que a tela precisa mostrar (e foi bug real do outro lado): sem a lista de
    pares, a tela não teria como saber que a CTS ficou órfã.
    """
    return await cadastro.cts(unidade_id)


@router.get("/unidades/{unidade_id}/alteracoes")
async def alteracoes(
    unidade_id: str,
    tipo: str | None = None,
    fichaId: str | None = None,  # noqa: N803 — camelCase e a convencao do contrato
    limite: int = cadastro.LIMITE_ALTERACOES,
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


@router.put("/unidades/{unidade_id}/sub-bacias/{sub_id}")
async def salvar_sub_bacia(
    unidade_id: str, sub_id: str, corpo: Corpo, usuario: Usuario
) -> dict[str, Any]:
    return await cadastro_escrita.salvar_coleta(
        unidade_id=unidade_id, ficha_id=sub_id, corpo=corpo, autor=usuario, e_cts=False
    )


@router.put("/unidades/{unidade_id}/cts/{cts_id}")
async def salvar_cts(
    unidade_id: str, cts_id: str, corpo: Corpo, usuario: Usuario
) -> dict[str, Any]:
    return await cadastro_escrita.salvar_coleta(
        unidade_id=unidade_id, ficha_id=cts_id, corpo=corpo, autor=usuario, e_cts=True
    )


@router.put("/unidades/{unidade_id}/contrato/{cidade_id}")
async def salvar_contrato(
    unidade_id: str, cidade_id: str, corpo: Corpo, usuario: Usuario
) -> dict[str, Any]:
    return await cadastro_escrita.salvar_contrato(
        unidade_id=unidade_id, cidade_id=cidade_id, corpo=corpo, autor=usuario
    )


@router.put("/unidades/{unidade_id}/etes/{ete_id}")
async def salvar_ete(
    unidade_id: str, ete_id: str, corpo: Corpo, usuario: Usuario
) -> dict[str, Any]:
    return await cadastro_escrita.salvar_ete(
        unidade_id=unidade_id, ete_id=ete_id, corpo=corpo, autor=usuario
    )


# ---------------------------------------------------------------------------
# CRIAR e APAGAR CTS: removidos de proposito
# ---------------------------------------------------------------------------
# A CTS NAO e algo que se cria escolhendo uma sub-bacia. Ela e um NO DO SISTEMA,
# como a sub-bacia, e a posicao dela ja esta em `input.sistema_topologia` — com
# jusante proprio: no banco carregado da planilha, TODAS estao la.
#
# (Houve um periodo com 339 fichas para 337 nos. As duas sobrando nao vinham
# da planilha — foram criadas pelo `POST /cts` que existia aqui, que gravava
# ficha e par sem tocar na topologia. E a prova pratica do argumento abaixo.)
#
# O motor confirma (`otimizador_capex_v62.py`): os nos saem do laco sobre
# `sistema-topologia`, e `cen.cts_ids = set(cts_operacional) & set(cen.nos)`. So e
# CTS efetiva a ficha que TAMBEM e no.
#
# Entao:
#   - `POST /cts` gravava `cts_operacional` + `subbacia_cts` e NAO tocava na
#     topologia: criava uma ficha visivel no cadastro e invisivel para a simulacao.
#   - `DELETE /cts` era pior. Apagava a ficha e deixava o no na topologia: a CTS
#     virava um no comum, sem ficha, sem componentes e com demanda ZERADA. E como
#     o par tambem sumia, com `usar_cts=False` a demanda dela nao era nem somada a
#     sub-bacia pareada. Destruia dado de duas formas ao mesmo tempo.
#
# `subbacia_cts` e SOBREPOSICAO de area, nao pertencimento: e ela que permite ao
# `USAR_CTS` decidir se a CTS entra como estrutura propria ou se ligacoes, receita
# e vazao dela sao somadas a sub-bacia irma.
#
# O que sobra e o que faz sentido: LER e EDITAR a ficha de uma CTS que o cadastro
# ja tem. Criar e remover CTS e mudanca de topologia, e topologia vem do
# Databricks como todo o resto do Grupo 01.
