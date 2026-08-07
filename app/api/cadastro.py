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

from app.api.deps import Usuario
from app.infra.repositorios import cadastro, cadastro_escrita

router = APIRouter(tags=["cadastro"])


async def _ou_404(valor, o_que: str):
    if valor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{o_que} não encontrado.")
    return valor


# ---------------------------------------------------------------- organização
@router.get("/regionais")
async def regionais() -> list[dict[str, Any]]:
    return await cadastro.regionais()


@router.get("/regionais/{regional_id}/unidades")
async def unidades(regional_id: str) -> list[dict[str, Any]]:
    return await cadastro.unidades(regional_id)


@router.get("/unidades/{unidade_id}")
async def unidade(unidade_id: str) -> dict[str, Any]:
    return await _ou_404(await cadastro.unidade(unidade_id), "Unidade")


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


# ---------------------------------------------------------------------------
# ESCRITA — uma ficha por vez, e o corpo e a ficha inteira
# ---------------------------------------------------------------------------
# O `autor` sai do TOKEN, nunca do corpo. Ele vai para a trilha de override, e
# aceita-lo do cliente seria aceitar que alguem assinasse a correcao de outro —
# numa trilha de auditoria isso e o defeito que a anula inteira.
#
# A resposta traz `overridesGravados` de proposito: e o unico jeito de quem chamou
# conferir que a trilha foi junto, sem consultar o banco.
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


@router.post("/unidades/{unidade_id}/cts", status_code=status.HTTP_201_CREATED)
async def criar_cts(unidade_id: str, corpo: Corpo, usuario: Usuario) -> dict[str, Any]:
    """Devolve a CTS CRIADA — e e essa versao que o front adota, nao a que enviou."""
    sub_id, cts = corpo.get("subId"), corpo.get("cts") or {}
    if not sub_id or not cts.get("id"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Informe a sub-bacia pareada e o identificador da CTS.",
        )
    return await cadastro_escrita.criar_cts(unidade_id=unidade_id, sub_id=sub_id, cts=cts)


@router.delete("/unidades/{unidade_id}/cts/{cts_id}", status_code=status.HTTP_204_NO_CONTENT)
async def apagar_cts(unidade_id: str, cts_id: str, usuario: Usuario) -> None:
    if not await cadastro_escrita.apagar_cts(unidade_id=unidade_id, cts_id=cts_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CTS não encontrada.")
