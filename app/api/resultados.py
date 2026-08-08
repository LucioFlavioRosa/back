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

from app.api.deps import Usuario, Quem
from app.dominio import run_id as rid
from app.infra.repositorios import niveis, resultado

router = APIRouter(tags=["resultados"])


@router.get("/runs")
async def historico(
    quem: Quem,
    unidade: str | None = Query(None),
    usuario: str | None = Query(None),
) -> list[dict[str, Any]]:
    """A lista do historico. Sai da view `otim_vw_historico`, que existe justamente
    para esta tela consumir sem nenhum join.

    O RECORTE NAO PASSA PELA `guarda_de_rota`: ela le parametros de rota, e aqui
    nao ha nenhum. Fica explicito, entao — e este comentario existe porque a lista
    foi a unica rota que o guarda nao alcancou, e uma lista que vaza e pior que um
    detalhe que vaza: entrega o mapa inteiro de uma vez.

    `usuario` da querystring vira FILTRO do proprio ("minhas rodadas"), nunca
    ampliacao: quem nao e admin ve so as suas, peca o que pedir.
    """
    if not quem.admin:
        usuario = quem.login
    linhas = await resultado.historico(unidade=unidade, usuario=usuario)
    if quem.admin or quem.tudo:
        return linhas
    # Rodada de unidade fora do escopo nao aparece nem que seja da propria pessoa
    # — o acesso pode ter sido revogado depois de ela rodar.
    return [l for l in linhas if l.get("unidadeId") in quem.unidades]


@router.get("/runs/{run_id}/meta")
async def meta(run_id: str) -> dict[str, Any]:
    """Alimenta o header de TODOS os niveis: chips de parametro e status do solver."""
    rid.exigir_valido(run_id)
    linha = await resultado.meta(run_id)
    if not linha:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rodada não encontrada.")
    return linha


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir(run_id: str, usuario: Usuario) -> None:
    """A unica mutacao de todo o pacote de resultados.

    Apaga o resultado; NAO toca no cadastro da unidade — a tela promete isso ao
    usuario no texto do modal de confirmacao, e o `ON DELETE CASCADE` das 13
    tabelas de detalhe aponta so para `otim_meta`.
    """
    rid.exigir_valido(run_id)
    if not await resultado.excluir(run_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rodada não encontrada.")


# ---------------------------------------------------------------------------
# A cascata: global -> cidade -> sistema -> sub-bacia -> elemento
# ---------------------------------------------------------------------------
# Todos seguem a mesma forma: valida o `run_id`, delega a consulta e devolve 404
# quando o recorte nao existe naquela rodada. O 404 importa: sem ele, uma cidade
# que nao pertence a rodada devolveria um objeto vazio, e a tela mostraria zeros
# como se fossem dado.


async def _ou_404(valor, o_que: str):
    if valor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{o_que} não encontrado nesta rodada.")
    return valor


@router.get("/runs/{run_id}/painel")
async def painel(run_id: str) -> dict[str, Any]:
    """Os 6 quadros do nivel global numa requisicao so.

    Desvio consciente do handoff, que sugeria `/ano`, `/mes`, `/obras/agregado` e
    `/subbacias/histograma` separados: sao quadros que aparecem sempre juntos, e o
    backend le as tabelas da mesma rodada de qualquer jeito.
    """
    rid.exigir_valido(run_id)
    return await niveis.painel(run_id)


@router.get("/runs/{run_id}/ebitda")
async def ebitda(run_id: str, cidade: str | None = Query(None)) -> dict[str, Any]:
    rid.exigir_valido(run_id)
    return await niveis.ebitda(run_id, cidade=cidade)


@router.get("/runs/{run_id}/cidades")
async def cidades(run_id: str) -> list[dict[str, Any]]:
    rid.exigir_valido(run_id)
    return await niveis.cidades(run_id)


@router.get("/runs/{run_id}/cidades/{cidade_id}")
async def cidade(run_id: str, cidade_id: str) -> dict[str, Any]:
    rid.exigir_valido(run_id)
    return await _ou_404(await niveis.cidade(run_id, cidade_id), "Cidade")


@router.get("/runs/{run_id}/sistemas/{sistema_id}/topologia")
async def topologia(run_id: str, sistema_id: str) -> dict[str, Any]:
    rid.exigir_valido(run_id)
    return await _ou_404(await niveis.topologia(run_id, sistema_id), "Sistema")


@router.get("/runs/{run_id}/subbacias/{sub_id}")
async def subbacia(run_id: str, sub_id: str) -> dict[str, Any]:
    rid.exigir_valido(run_id)
    return await _ou_404(await niveis.subbacia(run_id, sub_id), "Sub-bacia")


@router.get("/runs/{run_id}/obras/{obra_id}")
async def obra(run_id: str, obra_id: str) -> dict[str, Any]:
    rid.exigir_valido(run_id)
    return await _ou_404(await niveis.obra(run_id, obra_id), "Obra")
