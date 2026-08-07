"""Service Bus — o que separa o backend do Databricks.

O fluxo desenhado na arquitetura e:

    backend  --insere--> controle.run_request     (a rodada existe no banco)
             --publica-> Service Bus (fila)       (o pedido de execucao)
                              |
                              v
                         Job do Databricks        (le a run_request, roda, publica)
                              |
                              v
                         Service Bus (notificacao de termino)

Fila, e nao chamada direta a Jobs API, por uma razao de disponibilidade: se o
Databricks estiver indisponivel ou com o cluster subindo, a mensagem espera. Uma
chamada sincrona faria o usuario ver "falha ao iniciar" por um problema que se
resolve sozinho em dois minutos.

E a ORDEM importa: grava no banco PRIMEIRO, enfileira DEPOIS. Se enfileirasse
antes, o job poderia acordar e nao encontrar a `run_request` — que e o erro
`run_request nao encontrada para run_id=...` documentado no runbook do pacote de
producao. O contrario (gravou e falhou ao enfileirar) e recuperavel: a rodada fica
PENDENTE e visivel, e da para reenfileirar.
"""

import json
import logging

from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient

from app.config import config

log = logging.getLogger(__name__)

_cliente: ServiceBusClient | None = None


async def abrir() -> None:
    global _cliente
    if _cliente is None and config().service_bus_conn:
        _cliente = ServiceBusClient.from_connection_string(config().service_bus_conn)


async def fechar() -> None:
    global _cliente
    if _cliente is not None:
        await _cliente.close()
        _cliente = None


class FilaIndisponivel(RuntimeError):
    """A rodada foi gravada mas nao chegou a fila — estado recuperavel, e o texto
    do erro precisa dizer isso ao usuario."""


async def pedir_execucao(run_id: str, unidade_id: str, usuario: str) -> None:
    """Publica o pedido. O corpo carrega o minimo: quem consome busca o resto na
    `run_request`, que e a fonte de verdade — mensagem com copia dos parametros
    envelheceria em relacao ao banco."""
    if _cliente is None:
        raise FilaIndisponivel(
            "Fila de simulações não configurada neste ambiente (SERVICE_BUS_CONN vazio)."
        )

    corpo = json.dumps(
        {"run_id": run_id, "unidade_id": unidade_id, "solicitado_por": usuario},
        ensure_ascii=False,
    )
    msg = ServiceBusMessage(
        corpo,
        content_type="application/json",
        # Deduplicacao pelo run_id: um retry de rede do proprio backend nao pode
        # virar duas execucoes da mesma rodada no cluster.
        message_id=run_id,
        subject="executar_simulacao",
    )
    try:
        async with _cliente.get_queue_sender(config().fila_simulacoes) as sender:
            await sender.send_messages(msg)
    except Exception as e:  # noqa: BLE001 — a causa vai para o log, nao para o usuario
        log.exception("falha ao enfileirar run_id=%s", run_id)
        raise FilaIndisponivel(
            "A simulação foi registrada mas não pôde ser enviada para execução. "
            "Ela aparece no histórico como pendente e pode ser reexecutada."
        ) from e
