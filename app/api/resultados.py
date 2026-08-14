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

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query, status

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
    # `admin` ve as rodadas dos OUTROS; os demais, so as suas. Isto e POSSE.
    if not quem.admin:
        usuario = quem.login
    # As em voo vem de `controle.*` e as publicadas de `otim_*`: a rodada nasce na
    # primeira e migra para a segunda. Sem juntar as duas, quem fechasse o modal
    # perdia de vista o que estava rodando — a tela mais operacional do produto
    # era cega justamente para o estado operacional.
    # As favoritas sao de QUEM PEDIU, e nao do dono da rodada — por isso
    # `quem.login`, e nao a variavel `usuario`, que aqui e filtro. A diferenca so
    # aparece no admin, que ve as rodadas dos outros: a estrela na tela dele tem de
    # ser a dele.
    favoritas = await resultado.favoritas_de(quem.login)
    linhas = await resultado.em_voo(unidade=unidade, usuario=usuario, favoritas=favoritas)
    linhas += await resultado.historico(unidade=unidade, usuario=usuario, favoritas=favoritas)
    # E o ESCOPO vale para todo mundo, inclusive admin. Era
    # `if quem.admin or quem.tudo: return linhas`, e por causa daquele `admin` um
    # administrador de uma regional listava o banco inteiro — papel e escopo
    # viravam a mesma coisa, e a tabela de concessao deixava de significar algo
    # para quem mais precisa dela.
    #
    # Rodada de unidade fora do escopo nao aparece nem sendo da propria pessoa: a
    # concessao pode ter sido revogada depois de ela rodar.
    if quem.tudo:
        return linhas
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


#: Teto do comentario. Generoso porque o campo E para texto corrido — o `nome` da
#: rodada tem 200 justamente por ser rotulo de lista —, mas existe pela mesma
#: razao que la: sem limite, um colar acidental manda megabytes que atravessam
#: rede, log e banco de graca. Ver `simulacao.criar`.
_MAX_COMENTARIO = 4000


async def _pode_comentar(run_id: str, quem: Quem) -> None:
    """Comentar exige o mesmo alcance que LER a rodada — nem mais, nem menos.

    Nao e o caso de `favorita`, que dispensa checagem porque so mexe na lista de
    quem pede. Aqui o texto e compartilhado: quem escreve altera o que os outros
    veem, entao anotar uma rodada que a pessoa nao poderia sequer enxergar seria
    escrever num lugar invisivel para ela — e visivel para os outros.

    As duas regras sao as mesmas de `GET /runs`, na mesma ordem:
      POSSE   quem nao e admin so alcanca as proprias rodadas;
      ESCOPO  vale para todo mundo, admin inclusive — a concessao por unidade nao
              e sobreposta pelo papel.

    404 e nao 403 quando a rodada existe fora do alcance: dizer "existe, mas voce
    nao pode" ja entrega que ela existe, e o historico e recortado justamente para
    nao entregar isso.
    """
    linha = await resultado.dono_e_unidade(run_id)
    # `ve_rodada_de` e `acessa_unidade` sao os MESMOS metodos que a leitura usa —
    # incluindo o caso do dono nulo, que e so do admin. Reescrever a comparacao
    # aqui criaria uma segunda definicao de posse, e a que fica desatualizada e
    # sempre a copia.
    if (
        not linha
        or not quem.ve_rodada_de(linha.get("dono"))
        or not quem.acessa_unidade(linha.get("unidade_id"))
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rodada não encontrada.")


@router.put("/runs/{run_id}/comentario", status_code=status.HTTP_204_NO_CONTENT)
async def comentar(
    run_id: str, quem: Quem, corpo: Annotated[dict[str, Any], Body()]
) -> None:
    """A anotacao de quem analisa a rodada. `migracoes/010_run_comentario.sql`.

    `PUT` pela mesma razao que a favorita: o estado pedido e o estado final, e
    mandar duas vezes o mesmo texto nao produz nada diferente. Reescrever e o caso
    normal — o campo existe para ser editado depois de ver o resultado.

    TEXTO VAZIO APAGA, e nao grava string vazia. Sem isso "sem comentario" teria
    duas representacoes no banco (linha ausente e linha com ''), e a tela teria de
    conhecer as duas. O `DELETE` abaixo continua existindo para quem prefere dizer
    isso pelo verbo.
    """
    rid.exigir_valido(run_id)
    texto = str(corpo.get("texto") or "").strip()
    if len(texto) > _MAX_COMENTARIO:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"O comentário é longo demais (máximo {_MAX_COMENTARIO} caracteres).",
        )
    await _pode_comentar(run_id, quem)
    if texto:
        await resultado.comentar(run_id, texto, quem.login)
    else:
        await resultado.descomentar(run_id)


@router.delete("/runs/{run_id}/comentario", status_code=status.HTTP_204_NO_CONTENT)
async def descomentar(run_id: str, quem: Quem) -> None:
    """Apaga. Idempotente: apagar o que ja nao existe e o mesmo estado final."""
    rid.exigir_valido(run_id)
    await _pode_comentar(run_id, quem)
    await resultado.descomentar(run_id)


@router.put("/runs/{run_id}/favorita", status_code=status.HTTP_204_NO_CONTENT)
async def favoritar(run_id: str, quem: Quem) -> None:
    """Marca a rodada como favorita DE QUEM PEDIU.

    `PUT`, e nao `POST`, porque o verbo descreve o que acontece: o recurso
    "favorita desta rodada para esta pessoa" passa a existir, e pedir de novo nao
    muda mais nada. Duplo clique e retry de rede caem no mesmo estado, sem
    tratamento na API — a chave composta da tabela faz o trabalho.

    NAO ha checagem de posse aqui, e e deliberado: favoritar so afeta a propria
    lista de quem pede. O que protege o dado dos outros e a leitura — `GET /runs`
    ja recorta por posse e escopo, entao uma rodada que a pessoa nao pode ver nao
    aparece para ela nem favoritada. Marcar um `run_id` que ela nao ve nao revela
    nada sobre ele.
    """
    rid.exigir_valido(run_id)
    await resultado.favoritar(run_id, quem.login)


@router.delete("/runs/{run_id}/favorita", status_code=status.HTTP_204_NO_CONTENT)
async def desfavoritar(run_id: str, quem: Quem) -> None:
    """Desmarca. Idempotente pela mesma razao: o estado pedido e o estado final."""
    rid.exigir_valido(run_id)
    await resultado.desfavoritar(run_id, quem.login)


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
