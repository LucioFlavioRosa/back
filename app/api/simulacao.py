"""Disparo e acompanhamento de uma rodada.  `CONTRATO.md` §4.

A UNICA criacao de todo o app passa por aqui: um `POST` que gera um `run_id` novo,
que passa a existir para sempre no historico.

Ordem do disparo, e ela nao e negociavel:

    1. valida o cadastro (pendencia bloqueia — e regra de negocio, nao de UI)
    2. cunha o run_id
    3. GRAVA run_request + run_status=PENDENTE na MESMA transacao
    4. ENFILEIRA no Service Bus

Se 4 falhar, a rodada existe e aparece no historico como pendente: recuperavel.
Se a ordem fosse 4-3, o job poderia acordar antes do commit e nao encontrar a
`run_request` — o erro "run_request nao encontrada" do runbook de producao.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, status

from app.api.deps import Usuario
from app.dominio import run_id as rid
from app.dominio import status as st
from app.dominio.parametros import montar_params
from app.infra import fila
from app.infra.repositorios import controle

router = APIRouter(tags=["simulação"])


@router.get("/unidades/{unidade_id}/prontidao")
async def prontidao(unidade_id: str) -> dict[str, Any]:
    """Quantas pendencias o cadastro da unidade tem AGORA.

    Endpoint proprio, e nao um campo em `/unidades/{id}`, porque a resposta e
    volatil: muda a cada campo preenchido, e a tela precisa do numero do momento em
    que se clica Iniciar. O front busca com `staleTime: 0`.
    """
    unidade = await controle.unidade(unidade_id)
    if not unidade:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unidade não encontrada.")
    return {
        "unidadeId": unidade_id,
        "unidadeNome": unidade["nome"],
        "pendencias": await controle.pendencias_do_cadastro(unidade_id),
    }


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def criar(usuario: Usuario, corpo: Annotated[dict[str, Any], Body()]) -> dict[str, str]:
    unidade_id = corpo.get("unidade_id")
    if not unidade_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Informe a unidade.")

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

    # `montar_params` levanta ParametrosInvalidos (-> 422 com a mensagem), inclusive
    # para o que o job recusaria depois. Melhor recusar aqui que gravar uma rodada
    # destinada a morrer em ERRO.
    params = montar_params(corpo, unidade_id=unidade_id, usuario=usuario)

    run = rid.novo()
    await controle.abrir_rodada(
        run_id=run,
        unidade_id=unidade_id,
        params=params,
        usuario=usuario,
        rotulo=corpo.get("nome"),
    )
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
    return {"runId": run, "status": st.Status.PENDENTE}


@router.get("/runs/{run_id}/status")
async def status_da_rodada(run_id: str) -> dict[str, Any]:
    rid.exigir_valido(run_id)
    linha = await controle.status(run_id)
    if not linha:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rodada não encontrada.")
    return {
        "runId": run_id,
        "status": linha["status"],
        "progresso": linha.get("progresso") or 0,
        "erro": linha.get("erro"),
    }


@router.post("/runs/{run_id}/reexecutar", status_code=status.HTTP_202_ACCEPTED)
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
        await fila.pedir_execucao(run_id, unidade_id=linha["unidade"], usuario=usuario)
    except fila.FilaIndisponivel as e:
        await controle.marcar(run_id, st.Status.ERRO, erro=str(e))
        raise
    return {"runId": run_id, "status": st.Status.PENDENTE}


@router.post("/runs/{run_id}/cancelar", status_code=status.HTTP_204_NO_CONTENT)
async def cancelar(run_id: str) -> None:
    """PENDENTE — o banco ainda nao aceita este estado.

    `controle.run_status` tem `CHECK (status IN ('PENDENTE','RODANDO','SUCESSO',
    'FALHOU_QUALIDADE','ERRO'))`. `CANCELADA`, que o `CONTRATO.md` §4.3 lista e a
    tela usa, viola o CHECK: o UPDATE falharia.

    Responder 204 sem cancelar seria pior que responder erro — a tela fecharia o
    modal dizendo "cancelado" e o cluster continuaria processando, cobrando, e a
    rodada apareceria concluida minutos depois. Enquanto a migracao nao roda, este
    endpoint diz a verdade.

    Para ligar: (1) migracao acrescentando 'CANCELADA' ao CHECK; (2) decidir se
    cancelar tambem mata o job no Databricks (marcar o status so faz o front parar
    de perguntar — o cluster continua) — provavelmente `jobs/runs/cancel` via API.
    """
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "Cancelamento ainda não disponível: aguarda migração do banco e o "
        "cancelamento do job no Databricks.",
    )
