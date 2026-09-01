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

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.api.deps import Usuario, Quem, guarda_de_rota
from app.dominio import run_id as rid
from app.api import formas_resultado as formas
from app.infra.repositorios import niveis, resultado

#: PREFIXO E GUARDA MORAM NO ROTEADOR, e nao no `include_router`. Assim quem
#: le este arquivo ve sob que caminho as rotas abaixo vivem e que elas ja
#: nascem protegidas — sem precisar abrir o `main.py` para descobrir.
#:
#: `main.py` nao perde a visao do conjunto: `test_guarda_de_rota.py` cobra
#: que TODO roteador servido sob /api traga esta dependencia.
router = APIRouter(
    prefix="/api",
    tags=["resultados"],
    dependencies=[Depends(guarda_de_rota)],
)


# `exclude_unset` porque o contrato do front distingue AUSENTE de nulo: `parametros`
# e `metricas` sao `?` la — rodada nao publicada nao tem nenhum dos dois —, e o
# modelo, com default `None`, materializava `"metricas": null` onde antes a chave
# nem existia. `null` diria "existe e nao tem valor"; ausente diz "nao se aplica".
@router.get(
    "/runs",
    response_model=list[formas.RunResumo],
    response_model_exclude_unset=True,
)
async def historico(
    quem: Quem,
    unidade: Annotated[str | None, Query()] = None,
    usuario: Annotated[str | None, Query()] = None,
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


@router.get("/runs/{run_id}/meta", response_model=formas.RunMeta)
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


async def _run_publicado(run_id: str) -> str:
    """Valida o formato E a existencia da rodada, e devolve o `run_id`.

    DEPENDENCIA, e nao funcao chamada na primeira linha de cada rota. E a mesma
    razao pela qual `guarda_de_rota` vive no roteador e nao endpoint a endpoint
    (ver `main.py`): assim a rota nova NASCE com a checagem, em vez de depender de
    alguem lembrar. As sete rotas de agregado nasceram sem ela justamente por
    esquecimento, e devolviam 200 com zeros para um `run_id` inventado.

    Fica como parametro, e nao no roteador: `GET /runs` e `POST /runs` nao tem
    `run_id`, e as rotas de um recorte so (cidade, sistema, sub-bacia, obra) ja
    respondem 404 por `_ou_404` — passar por aqui as faria pagar uma consulta a
    mais para chegar ao mesmo lugar.

    As rotas que devolvem AGREGADO — painel, ebitda, lista de cidades,
    explicabilidade, lista de obras, cronograma — nao tem um registro unico para
    achar ou nao achar, entao sem esta checagem elas respondiam 200 com zeros
    para um `run_id` inventado. Zero de sub-bacia presa e zero de obra sao
    respostas legitimas de uma rodada de verdade; devolve-las para uma rodada que
    nao existe torna as duas coisas indistinguiveis.

    As rotas de um recorte so (cidade, sistema, sub-bacia, obra) nao precisam
    dele: `_ou_404` ja nao acha o registro quando a rodada nao existe.
    """
    rid.exigir_valido(run_id)
    if not await niveis.existe(run_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rodada não encontrada.")
    return run_id


#: O `run_id` de uma rodada que existe. Alias para reuso, no estilo de
#: `Usuario`/`Quem` em `deps.py`.
RunPublicado = Annotated[str, Depends(_run_publicado)]


async def _ou_404(valor, o_que: str):
    if valor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{o_que} não encontrado nesta rodada.")
    return valor


@router.get("/runs/{run_id}/painel", response_model=formas.PainelGlobal)
async def painel(run_id: RunPublicado) -> dict[str, Any]:
    """Os 6 quadros do nivel global numa requisicao so.

    Desvio consciente do handoff, que sugeria `/ano`, `/mes`, `/obras/agregado` e
    `/subbacias/histograma` separados: sao quadros que aparecem sempre juntos, e o
    backend le as tabelas da mesma rodada de qualquer jeito.
    """
    return await niveis.painel(run_id)


@router.get("/runs/{run_id}/ebitda", response_model=formas.PainelEbitda)
async def ebitda(
    run_id: RunPublicado, cidade: Annotated[str | None, Query()] = None
) -> dict[str, Any]:
    return await niveis.ebitda(run_id, cidade=cidade)


@router.get("/runs/{run_id}/cidades", response_model=list[formas.CidadeLinha])
async def cidades(run_id: RunPublicado) -> list[dict[str, Any]]:
    return await niveis.cidades(run_id)


@router.get("/runs/{run_id}/cidades/{cidade_id}", response_model=formas.CidadeDetalhe)
async def cidade(run_id: str, cidade_id: str) -> dict[str, Any]:
    rid.exigir_valido(run_id)
    return await _ou_404(await niveis.cidade(run_id, cidade_id), "Cidade")


@router.get("/runs/{run_id}/sistemas/{sistema_id}/topologia", response_model=formas.Fluxo)
async def topologia(run_id: str, sistema_id: str) -> dict[str, Any]:
    rid.exigir_valido(run_id)
    return await _ou_404(await niveis.topologia(run_id, sistema_id), "Sistema")


@router.get("/runs/{run_id}/subbacias/{sub_id}", response_model=formas.SubBaciaDetalhe)
async def subbacia(run_id: str, sub_id: str) -> dict[str, Any]:
    rid.exigir_valido(run_id)
    return await _ou_404(await niveis.subbacia(run_id, sub_id), "Sub-bacia")


@router.get("/runs/{run_id}/explicabilidade", response_model=formas.ExplicabilidadeGlobal)
async def explicabilidade(run_id: RunPublicado) -> dict[str, Any]:
    """Por que o plano nao conecta 100% — agregado por motivo, nivel global.

    Sem `_ou_404`: uma rodada sem nenhuma sub-bacia presa e uma resposta legitima
    (`naoFaturando: 0`), e a tela a trata como "nada a explicar". 404 aqui diria
    que a rodada nao existe.
    """
    return await niveis.explicabilidade(run_id)


@router.get("/runs/{run_id}/cidades/{cidade_id}/explicabilidade", response_model=formas.ExplicabilidadeGlobal)
async def explicabilidade_da_cidade(run_id: RunPublicado, cidade_id: str) -> dict[str, Any]:
    """O mesmo recorte dentro de uma cidade.

    Endpoint proprio, e nao `?cidade=` no de cima: a URL da cidade ja e
    `/cidades/{id}`, e colar o recorte nela segue o padrao de `/cidades/{id}`.
    Aqui o 404 vale — cidade que nao pertence a rodada nao tem explicacao nenhuma.
    """
    return await _ou_404(await niveis.explicabilidade(run_id, cidade_id), "Cidade")


# As duas rotas de obra abaixo vem ANTES de `/obras/{obra_id}`, e a ordem e o que
# as faz existir: o FastAPI casa na ordem de declaracao, e `/obras/{obra_id}`
# aceitaria "cronograma" como se fosse um id — resposta 404 "Obra nao encontrada"
# para um endpoint que existe.
@router.get("/runs/{run_id}/obras/cronograma", response_model=formas.CronogramaDeObras)
async def cronograma_de_obras(run_id: RunPublicado) -> dict[str, Any]:
    return await niveis.cronograma_de_obras(run_id)


@router.get("/runs/{run_id}/obras", response_model=formas.ObrasPagina)
async def obras(
    run_id: RunPublicado,
    situacao: Annotated[str | None, Query()] = None,
    cidade: Annotated[str | None, Query()] = None,
    ano: Annotated[int | None, Query()] = None,
    pagina: Annotated[int, Query(ge=1)] = 1,
    tamanho: Annotated[int, Query(ge=1, le=500)] = 50,
    ordenar: Annotated[str, Query()] = "inicio",
) -> dict[str, Any]:
    """A lista de obras do plano, paginada. `total` e o do resultado FILTRADO."""
    return await niveis.obras(
        run_id,
        situacao=situacao,
        cidade=cidade,
        ano=ano,
        pagina=pagina,
        tamanho=tamanho,
        ordenar=ordenar,
    )


@router.get("/runs/{run_id}/obras/{obra_id}", response_model=formas.ObraDetalhe)
async def obra(run_id: str, obra_id: str) -> dict[str, Any]:
    rid.exigir_valido(run_id)
    return await _ou_404(await niveis.obra(run_id, obra_id), "Obra")
