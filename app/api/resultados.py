"""Leitura de uma rodada publicada.  `CONTRATO.md` §3.

Somente leitura, exceto o `DELETE`. Tudo por `run_id`; a unidade nao entra na URL
porque uma rodada pertence a exatamente uma unidade — o `run_id` ja determina o
recorte.

A cascata de niveis (global -> cidade -> sistema -> sub-bacia -> elemento) e a
mesma do `leitor_v2.py`, que e o contrato de leitura escrito pelo autor do motor e
validado no notebook (`Otimizador_CAPEX_v61_dashboard.ipynb`, PARTE IV). Cada
funcao de la vira um endpoint aqui, com a diferenca de que la a fonte era o dict de
DataFrames e aqui e o Postgres — as tabelas sao as mesmas 14 `public.otim_*`.

    L.listar_runs / L.kpis / L.painel_geral / L.ebitda  ->  §3.1 §3.3 §3.4 §3.5
    L.cidades / L.cidade / L.cobertura_cidade           ->  §3.6 §3.7
    L.sistemas / L.topologia_sistema                    ->  §3.8
    L.subbacias / L.explicar / L.deep_dive              ->  §3.9
    L.elementos / L.elemento                            ->  §3.10

O front cacheia tudo isto com `staleTime: Infinity` — o que so e correto porque um
`run_id` publicado e imutavel (`CONTRATO.md` §2.1, e a regra vive em
`app/dominio/status.py`).
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.dominio import run_id as rid
from app.infra.repositorios import resultado

router = APIRouter(tags=["resultados"])


@router.get("/runs")
async def historico(
    unidade: str | None = Query(None),
    usuario: str | None = Query(None),
) -> list[dict[str, Any]]:
    """A lista do historico. Sai da view `otim_vw_historico`, que existe justamente
    para esta tela consumir sem nenhum join."""
    return await resultado.historico(unidade=unidade, usuario=usuario)


@router.get("/runs/{run_id}/meta")
async def meta(run_id: str) -> dict[str, Any]:
    """Alimenta o header de TODOS os niveis: chips de parametro e status do solver."""
    rid.exigir_valido(run_id)
    linha = await resultado.meta(run_id)
    if not linha:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rodada não encontrada.")
    return linha


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir(run_id: str) -> None:
    """A unica mutacao de todo o pacote de resultados.

    Apaga o resultado; NAO toca no cadastro da unidade — a tela promete isso ao
    usuario no texto do modal de confirmacao, e o `ON DELETE CASCADE` das 13
    tabelas de detalhe aponta so para `otim_meta`.
    """
    rid.exigir_valido(run_id)
    if not await resultado.excluir(run_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rodada não encontrada.")


# ---------------------------------------------------------------------------
# PENDENTE — os niveis fundos da cascata
# ---------------------------------------------------------------------------
# Faltam §3.4 painel, §3.5 ebitda, §3.6/§3.7 cidades, §3.8 topologia, §3.9
# sub-bacia e §3.10 obra. Nenhum deles e incerto: as consultas saem das mesmas
# tabelas e a forma da resposta esta escrita no `CONTRATO.md` com exemplo de JSON
# e no `src/resultado/domain/resultado.ts` do front com os tipos.
#
# Estao de fora desta leva por uma razao so — sao 7 endpoints com montagem de
# payload aninhado (a topologia monta nos, componentes e ETE a partir de
# `otim_dependencia` + `otim_obra` + `otim_sistema`), e entrega-los pela metade
# seria pior que declarar o que falta. O `leitor_v2.py` ja tem a logica de
# agregacao de cada um, em pandas: e dele que as consultas devem sair, e nao de
# uma leitura nova do esquema.
