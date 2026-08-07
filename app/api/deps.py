"""Dependencias compartilhadas pelos endpoints.

A que carrega peso e o USUARIO. Ele nao e enfeite de auditoria: o pedido do
projeto e que cada simulacao fique amarrada a uma pessoa, e e ele que vai para
`controle.run_request.solicitado_por` e, dali, para `otim_meta.usuario` — a coluna
que a tela de historico mostra como autor.

Por isso o usuario sai do TOKEN e nunca do corpo da requisicao. Aceitar do corpo
seria aceitar que qualquer um assinasse a simulacao de outro, e o historico
deixaria de responder "quem pediu isto".

Enquanto o Entra ID nao esta configurado (`ENTRA_TENANT_ID` vazio), o servico roda
sem exigir token e usa um usuario de desenvolvimento — o mesmo arranjo do front,
que so manda `Authorization` quando o SSO esta ligado no `/config.js`. O `/readyz`
denuncia esse modo para que ele nao chegue a producao sem que alguem veja.
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import config

USUARIO_DEV = "dev@local"


async def usuario_atual(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    cfg = config()

    if not cfg.exige_auth:
        return USUARIO_DEV

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão inválida ou expirada.")

    token = authorization.split(" ", 1)[1].strip()
    try:
        return await _identidade_do_token(token)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão inválida ou expirada.") from e


async def _identidade_do_token(token: str) -> str:
    """Valida o access token do Entra ID e devolve quem e o usuario.

    PENDENTE — precisa da validacao de assinatura via JWKS do tenant
    (`https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys`), com cache
    das chaves e conferencia de `aud`, `iss` e `exp`.

    Levanta em vez de decodificar sem verificar: um `jwt.decode(..., verify=False)`
    aqui aceitaria qualquer token forjado, e o modo de falha seria silencioso —
    tudo funcionando, com o usuario que o atacante escolheu. Falhar alto mantem o
    ambiente sem SSO explicitamente sem SSO.
    """
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "Validação de token do Entra ID ainda não implementada neste serviço.",
    )


Usuario = Annotated[str, Depends(usuario_atual)]
