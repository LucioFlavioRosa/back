"""Ciclo de vida de uma rodada, e quem pode reexecutar quando.

O estado observavel de uma rodada e `controle.run_status.status`. A publicacao do
job e atomica — `public.otim_*` e `status = SUCESSO` entram na mesma transacao —
entao "ja foi publicado" e um FATO CONSULTAVEL, e nao um julgamento sobre a
intencao de quem disparou. E por isso que a regra de imutabilidade se apoia nele.

ATENCAO — divergencia conhecida entre o contrato do front e o banco:

    CONTRATO.md 4.3 lista CANCELADA entre os status possiveis;
    ddl_input.sql tem CHECK (status IN ('PENDENTE','RODANDO','SUCESSO',
                                        'FALHOU_QUALIDADE','ERRO'))

Ou seja, gravar CANCELADA hoje viola o CHECK e o INSERT falha. Enquanto a migracao
nao rodar, `POST /runs/{id}/cancelar` nao pode fingir que cancelou: ver
`app/api/simulacao.py`. Deixar o codigo tentar e capturar o erro seria pior — o
usuario veria "cancelado" e a rodada continuaria consumindo cluster.
"""

from enum import StrEnum


class Status(StrEnum):
    PENDENTE = "PENDENTE"
    RODANDO = "RODANDO"
    SUCESSO = "SUCESSO"
    FALHOU_QUALIDADE = "FALHOU_QUALIDADE"
    ERRO = "ERRO"
    # Ainda NAO aceito pelo CHECK do banco. Ver o docstring do modulo.
    CANCELADA = "CANCELADA"


#: O que o banco aceita hoje, sem migracao.
ACEITOS_PELO_BANCO = frozenset(
    {Status.PENDENTE, Status.RODANDO, Status.SUCESSO, Status.FALHOU_QUALIDADE, Status.ERRO}
)

#: Terminais: o front para o polling nestes.
TERMINAIS = frozenset({Status.SUCESSO, Status.FALHOU_QUALIDADE, Status.ERRO, Status.CANCELADA})

#: Em voo: existe trabalho acontecendo (ou esperando na fila).
EM_VOO = frozenset({Status.PENDENTE, Status.RODANDO})


def congelada(status: str | None) -> bool:
    """A rodada publicou? Entao o `run_id` dela nao aceita mais nenhuma execucao."""
    return status == Status.SUCESSO


def pode_reexecutar(status: str | None) -> bool:
    """Retry tecnico: so enquanto nada foi publicado.

    `None` (rodada sem linha em run_status) conta como "nao publicou" — e o intervalo
    entre o INSERT da run_request e o job pegar a mensagem.
    """
    return not congelada(status) and status not in EM_VOO


def motivo_para_recusar_reexecucao(status: str | None) -> str | None:
    """A mensagem que vai no corpo do 409. Ela e lida pelo usuario, entao diz o que
    fazer, e nao so o que aconteceu."""
    if congelada(status):
        return (
            "Esta rodada já foi publicada e não pode ser reexecutada. "
            "Crie uma nova simulação — o resultado publicado precisa continuar "
            "existindo para quem já o consultou."
        )
    if status in EM_VOO:
        return f"Esta rodada ainda está em execução (status {status}). Aguarde o término."
    return None
