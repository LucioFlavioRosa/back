"""Cadastro da unidade — o que alimenta a simulação.  `DEPLOY.md` §3 do front.

São dois lados com naturezas diferentes:

    LEITURA   8 endpoints, um por grupo de fichas. É o que esta leva entrega.
    ESCRITA   6 endpoints, uma ficha por vez (o corpo é a ficha inteira, não um
              patch). BLOQUEADA — ver o final deste módulo.

A ficha de coleta (sub-bacia e CTS, que são iguais) tem dois blocos de origem
diferente, e isso atravessa todo o cadastro:

  `db`      vem do Databricks, é travado na tela e corrigível só por override
  `params`  a Regional preenche

`params` viaja sempre inteiro, inclusive `popU`/`popA`. A régua de cobertura da
cidade decide se esses dois **aparecem** e se contam pendência — não se são
enviados. É de propósito: trocar a régua de uma cidade não pode apagar o que
alguém já preencheu.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.infra.repositorios import cadastro

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
# ESCRITA — bloqueada, e não esquecida
# ---------------------------------------------------------------------------
# Faltam os 6: PUT de sub-bacia, contrato, ETE e CTS, mais POST e DELETE de CTS.
# O que impede não é o volume: é que **não existe tabela para a trilha de
# override**.
#
# O contrato manda `overrides` junto com toda ficha (campo, valor antigo, valor
# novo, autor, timestamp) e é explícito sobre o porquê: "gravar na mesma transação
# do dado evita dado corrigido sem trilha". O `ddl_input.sql` tem 16 tabelas e
# nenhuma delas guarda isso.
#
# As saídas são duas, e a escolha não é minha:
#   (a) migração criando `input.override` (ficha, campo, valor_antigo, valor_novo,
#       autor, gravado_em) — a trilha vira consultável e a promessa se cumpre;
#   (b) aceitar a ficha e DESCARTAR o override — e aí o contrato precisa parar de
#       prometer trilha, porque prometer auditoria que não existe é pior que não
#       ter auditoria: alguém vai confiar nela numa discussão sobre um número.
#
# Escrever os PUTs agora significaria escolher (b) em silêncio. Está no README.
