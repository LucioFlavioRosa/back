"""Disparo e acompanhamento de uma rodada.  `CONTRATO.md` §4.

A UNICA criacao de todo o app passa por aqui: um `POST` que gera um `run_id` novo,
que passa a existir para sempre no historico.

Ordem do disparo, e ela nao e negociavel:

    1. valida o cadastro (pendencia bloqueia — e regra de negocio, nao de UI)
    2. cunha o run_id
    3. GRAVA run_request + run_status=PENDENTE na MESMA transacao
    4. ENFILEIRA no Service Bus

Se 4 falhar, a rodada existe e e marcada como ERRO, com a causa — nao fica
PENDENTE. Deixa-la PENDENTE seria armadilha: o `/reexecutar` recusa PENDENTE por
considera-la em voo, e ela ficaria parada para sempre com a tela dizendo que da
para tentar de novo. Como ERRO ela sai do "em voo", aparece no historico com o
motivo, e o botao de reexecutar volta a funcionar. Ver o `except` em `criar`.

Se a ordem fosse 4-3, o job poderia acordar antes do commit e nao encontrar a
`run_request` — o erro "run_request nao encontrada" do runbook de producao.
"""

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status

from app.api.deps import Quem, Usuario, exigir_unidade, guarda_de_rota
from app.api import formas_cadastro as formas
from app.dominio import run_id as rid
from app.dominio import status as st
from app.dominio.parametros import montar_params
from app.infra import fila
from app.infra.repositorios import controle, pendencias

#: PREFIXO E GUARDA MORAM NO ROTEADOR, e nao no `include_router`. Assim quem
#: le este arquivo ve sob que caminho as rotas abaixo vivem e que elas ja
#: nascem protegidas — sem precisar abrir o `main.py` para descobrir.
#:
#: `main.py` nao perde a visao do conjunto: `test_guarda_de_rota.py` cobra
#: que TODO roteador servido sob /api traga esta dependencia.
router = APIRouter(
    prefix="/api",
    tags=["simulação"],
    dependencies=[Depends(guarda_de_rota)],
)


@router.get("/unidades/{unidade_id}/prontidao", response_model=formas.Prontidao)
async def prontidao(unidade_id: str) -> dict[str, Any]:
    """Quantas pendencias o cadastro da unidade tem AGORA.

    Endpoint proprio, e nao um campo em `/unidades/{id}`, porque a resposta e
    volatil: muda a cada campo preenchido, e a tela precisa do numero do momento em
    que se clica Iniciar. O front busca com `staleTime: 0`.
    """
    unidade = await controle.unidade(unidade_id)
    if not unidade:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unidade não encontrada.")
    conta = await pendencias.contar(unidade_id)
    return {
        "unidadeId": unidade_id,
        "unidadeNome": unidade["nome"],
        "pendencias": conta["pendencias"],
        # `porGrupo` alem do contrato, de proposito: com so o total, a tela diz
        # "faltam 12 campos" e o usuario tem de procurar em cinco grupos. O front
        # ignora campo que nao conhece, entao acrescentar nao quebra nada.
        "porGrupo": conta["porGrupo"],
        # `faltando` traz o que a tela NAO tem como descobrir: o componente de
        # obra que a ficha nao tem. Campo em branco ela conta sozinha, a cada
        # tecla; componente ausente nao aparece no payload da ficha — a ficha
        # chega com quatro linhas em vez de cinco e nada diz que havia uma quinta.
        # Sem isto, o `PUT` recusa a ficha incompleta e a pessoa nao sabe o que
        # corrigir. Ver `pendencias.componentes_faltando`.
        "faltando": conta["faltando"],
    }


@router.post("/runs", status_code=status.HTTP_201_CREATED, response_model=formas.RodadaCriada)
async def criar(
    quem: Quem, corpo: Annotated[dict[str, Any], Body()], resposta: Response
) -> dict[str, Any]:
    # `dict[str, Any]`, e nao `dict[str, str]`: o FastAPI usa a anotacao como
    # response_model e COAGE os valores. Com `str`, o `jaExistia` booleano sairia
    # como a string `"true"` — e toda string nao vazia e verdadeira em JavaScript,
    # entao o front leria "ja existia" tambem quando a rodada acabou de nascer.
    unidade_id = corpo.get("unidade_id")
    if not unidade_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Informe a unidade.")

    # A UNIDADE VEM DO CORPO, e por isso a `guarda_de_rota` NAO alcanca esta rota:
    # ela le `request.path_params`, e aqui nao ha `{unidade_id}` no caminho.
    #
    # Sem esta linha, alguem com acesso a uma unidade disparava simulacao em
    # QUALQUER outra — e, como o disparo grava `solicitado_por`, virava dono da
    # rodada e passava a poder ler o resultado dela. Escopo furado que se
    # transformava em posse legitima.
    #
    # Foi o unico caso do tipo: as demais rotas recebem a unidade pelo caminho.
    # Se aparecer outra que a receba pelo corpo, ela precisa desta linha tambem —
    # o guarda de roteador nao substitui isto, e nao tem como.
    exigir_unidade(quem, unidade_id)
    usuario = quem.login

    # O front ja bloqueia, mas o contrato e claro: isto e regra de negocio e
    # precisa ser checada no servidor. O mock do front recusa com 422 de proposito,
    # justamente para que o dia em que a tela deixasse passar nao ficasse escondido.
    pendencias = await controle.pendencias_do_cadastro(unidade_id)
    if pendencias:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"O cadastro desta unidade tem {pendencias} pendência(s). "
            "Complete o cadastro antes de simular.",
        )

    # Nome de rodada e rotulo de tela, nao campo livre: 2 MB foram aceitos num
    # teste de uso real. Mesmo o backend descartando o valor hoje, aceitar sem
    # limite e custo de rede e ruido de log de graca.
    nome = corpo.get("nome")
    if nome and len(str(nome)) > 200:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "O nome da simulação é longo demais (máximo 200 caracteres).",
        )

    # `montar_params` levanta ParametrosInvalidos (-> 422 com a mensagem), inclusive
    # para o que o job recusaria depois. Melhor recusar aqui que gravar uma rodada
    # destinada a morrer em ERRO.
    params = montar_params(corpo, unidade_id=unidade_id, usuario=usuario)

    pedido = rid.novo()
    aberta = await controle.abrir_rodada(
        run_id=pedido,
        unidade_id=unidade_id,
        params=params,
        usuario=usuario,
        rotulo=corpo.get("nome"),
    )
    run = aberta["run_id"]
    if aberta["ja_existia"]:
        # Pedido IDENTICO da MESMA PESSOA. Dois casos, e agora os dois caem aqui:
        #
        #  em voo      duplo clique, retry do navegador, reenvio do SDK — o
        #              segundo clique leva ao mesmo lugar;
        #  concluida   a mesma simulacao ja rodou e esta publicada, e o cadastro
        #              nao mudou desde entao (R5). Em vez de gastar o cluster para
        #              produzir o mesmo resultado, aponta para o que existe.
        #
        # Duas PESSOAS pedindo a mesma coisa NAO caem aqui: o `USUARIO` entra no
        # digest. Cair aqui devolveria a Ciclana o `runId` do Fulano, e o guarda
        # de posse responderia 404 na tela seguinte — dizer "pronto, e essa" e
        # depois negar que existe e pior que gastar cluster duas vezes.
        #
        # 200 e nao 201, porque nada foi criado. E nao 409: nao ha conflito a
        # resolver, ha uma rodada pronta para abrir. Rodar a mesma unidade com
        # parametros DIFERENTES continua livre — comparar cenarios e o uso normal.
        #
        # `jaExistia` no CORPO, e nao so o codigo 200: o cliente do front devolve
        # o JSON e descarta o status (`comum/api/client.ts`), entao um front que
        # so olhasse o codigo precisaria mudar o transporte inteiro para saber o
        # que ja da para dizer aqui. O codigo continua correto para quem le HTTP.
        resposta.status_code = status.HTTP_200_OK
        return {"runId": run, "status": aberta["status"], "jaExistia": True}

    try:
        await fila.pedir_execucao(run, unidade_id=unidade_id, usuario=usuario)
    except fila.FilaIndisponivel as e:
        # A rodada JA esta gravada. Deixa-la em PENDENTE seria uma armadilha: o
        # `/reexecutar` recusa PENDENTE por considera-la em voo, entao ela ficaria
        # parada para sempre com a tela dizendo que da para tentar de novo.
        # Marcada como ERRO, ela sai do "em voo", aparece no historico com a causa
        # e o botao de reexecutar volta a funcionar.
        await controle.marcar(run, st.Status.ERRO, erro=str(e))
        raise
    return {"runId": run, "status": st.Status.PENDENTE, "jaExistia": False}


def _plural(n: int, um: str, muitos: str) -> str:
    """`"1 vaga livre"` · `"4 vagas livres"`.

    A frase da fila e lida na tela, e "Todas as 1 vagas estao ocupadas" — que era o
    que saia com um executor so — e o tipo de descuido que faz duvidar do resto dos
    numeros. O front ja tem esta mesma funcao para o porte da unidade, e pela mesma
    razao; aqui ela precisa existir de novo porque quem monta a frase e o servidor,
    que e o unico que ve a fila inteira.

    A forma `vaga(s)` que estava aqui evitava o erro sem resolver a leitura.
    """
    return f"{n} {um if n == 1 else muitos}"


def _todas_ocupadas(capacidade: int) -> str:
    """A primeira metade da frase de fila cheia, com a concordancia certa.

    Com uma vaga so, "Todas as 1 vagas estao ocupadas" erra duas vezes — o
    "todas" e o plural. Com uma vaga nao ha "todas": ha A vaga.
    """
    if capacidade == 1:
        return "A única vaga está ocupada."
    return f"Todas as {capacidade} vagas estão ocupadas."


@router.get(
    "/runs/{run_id}/status",
    response_model=formas.StatusDaRodada,
    response_model_exclude_unset=True,
)
async def status_da_rodada(run_id: str) -> dict[str, Any]:
    rid.exigir_valido(run_id)
    linha = await controle.status(run_id)
    if not linha:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rodada não encontrada.")
    resposta = {
        "runId": run_id,
        "status": linha["status"],
        "progresso": linha.get("progresso") or 0,
        "erro": linha.get("erro"),
        # Desde quando ela existe. A tela precisa disto para mostrar tempo
        # decorrido: sem ele, "esperando" com dois segundos e "esperando" com
        # quarenta minutos sao a mesma frase.
        "pedidaEm": (
            linha["solicitado_em"].isoformat() if linha.get("solicitado_em") else None
        ),
    }
    if linha["status"] not in ("PENDENTE", "RODANDO"):
        return resposta

    # O BLOCO `fila`: POR QUE esta rodada esta onde esta.
    #
    # "Na fila, esperando um executor" cobria dois mundos opostos — todos os
    # executores ocupados (espere) e nenhum executor de pe (isto nunca vai rodar)
    # — e quem olhava a tela nao tinha como saber em qual estava. Em producao,
    # com o job do Databricks, o segundo caso e silencioso e caro.
    exec_ = await controle.executores()
    fila = {**exec_, "posicao": 0, "motivo": "", "atencao": False}

    if linha["status"] == "RODANDO":
        fila["motivo"] = "Em execução."
        # Lease vencido: o executor parou de responder. Dizer isto na hora e melhor
        # que esperar o watchdog — a tela ja pode oferecer reexecutar.
        if linha.get("lease_ate") and linha["lease_ate"] < datetime.now(timezone.utc):
            fila["motivo"] = (
                "O executor que pegou esta rodada parou de responder. "
                "Ela será liberada para reexecução."
            )
            fila["atencao"] = True
    elif exec_["vivos"] == 0:
        # O caso que o dono do produto marcou como inaceitavel em producao.
        fila["motivo"] = (
            "NENHUM executor está ativo. A rodada não vai começar enquanto um não "
            "subir — isto não é fila cheia, é ausência de executor."
        )
        fila["atencao"] = True
    else:
        fila["posicao"] = await controle.posicao_na_fila(run_id)
        livres = max(exec_["capacidade"] - exec_["ocupadas"], 0)
        ocupadas = _todas_ocupadas(exec_["capacidade"])
        if livres > 0:
            fila["motivo"] = (
                f"Há {_plural(livres, 'vaga livre', 'vagas livres')} — "
                "deve começar em instantes."
            )
        elif fila["posicao"] == 0:
            fila["motivo"] = f"{ocupadas} Esta é a próxima a entrar."
        else:
            frente = _plural(fila["posicao"], "simulação", "simulações")
            fila["motivo"] = f"{ocupadas} Há {frente} na frente desta."

    resposta["fila"] = fila
    return resposta


@router.post("/runs/{run_id}/reexecutar", status_code=status.HTTP_202_ACCEPTED, response_model=formas.RodadaAceita)
async def reexecutar(run_id: str, usuario: Usuario) -> dict[str, str]:
    """Retry tecnico — reusa o mesmo `run_id`.  `CONTRATO.md` §4.5.

    So enquanto a rodada NAO publicou. Depois do SUCESSO o `run_id` congelou e a
    resposta e 409: republicar apagaria o resultado que alguem ja consultou, e a
    copia congelada em blob junto.
    """
    rid.exigir_valido(run_id)
    linha = await controle.status(run_id)
    if not linha:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rodada não encontrada.")

    motivo = st.motivo_para_recusar_reexecucao(linha["status"])
    if motivo:
        raise HTTPException(status.HTTP_409_CONFLICT, motivo)

    await controle.marcar(run_id, st.Status.PENDENTE, erro=None)
    try:
        await fila.pedir_execucao(
            run_id, unidade_id=linha["unidade"], usuario=usuario, reenvio=True
        )
    except fila.FilaIndisponivel as e:
        await controle.marcar(run_id, st.Status.ERRO, erro=str(e))
        raise
    return {"runId": run_id, "status": st.Status.PENDENTE}


@router.post("/runs/{run_id}/cancelar", status_code=status.HTTP_204_NO_CONTENT)
async def cancelar(run_id: str) -> None:
    """O usuario desiste de uma rodada que ainda nao terminou. `CONTRATO.md` §4.4.

    Respondeu 501 enquanto `controle.run_status` tinha um CHECK sem `CANCELADA` —
    o UPDATE falharia, e responder 204 sem cancelar seria pior que responder erro:
    a tela fecharia dizendo "cancelado" e o cluster seguiria processando e
    cobrando. `migracoes/008_lease_e_executores.sql` pos o valor no CHECK.

    O QUE ESTE 204 GARANTE, e o que nao:

      PENDENTE  a rodada nao vai executar. A mensagem continua na fila, mas
                `processar` no executor ja recusa quem chega com desfecho gravado
                — e `CANCELADA` e um desfecho. Cancelamento completo.
      RODANDO   a rodada nao vai PUBLICAR. O executor confere o status nos pontos
                em que a rodada respira (entre solver e materializacao, e a cada
                passo da barra) e larga o trabalho. O solver em voo nao e
                interrompivel no meio — e chamada nativa —, entao a espera e
                limitada pelo `MAX_TIME_S` da propria rodada.

    Prometer mais que isso exigiria matar o processo do executor, e a diferenca
    que importa para quem clicou — nada e publicado, a vaga e devolvida — ja esta
    garantida aqui.
    """
    rid.exigir_valido(run_id)
    linha = await controle.status(run_id)
    if not linha:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rodada não encontrada.")

    motivo = st.motivo_para_recusar_cancelamento(linha["status"])
    if motivo:
        raise HTTPException(status.HTTP_409_CONFLICT, motivo)

    # O `if` acima e para a MENSAGEM; a garantia e o UPDATE condicional. Entre ler
    # o status e escrever, o executor pode ter publicado — e sobrescrever SUCESSO
    # por CANCELADA deixaria o resultado gravado e invisivel.
    if not await controle.cancelar(run_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A rodada mudou de estado enquanto o cancelamento era processado — "
            "ela terminou por conta própria. Confira o status.",
        )
