"""Validacao do access token do provedor de identidade.

O que este modulo faz, e so isso: recebe a string do `Authorization: Bearer`,
prova que ela foi assinada por quem deveria e devolve QUEM e o usuario. Ele nao
sabe nada sobre escopo, papel ou unidade — isso e `deps._com_acesso`, e sai da
tabela `controle.usuario_acesso`, nao do diretorio corporativo.

## Por que OIDC e nao "codigo do Entra"

A verificacao e padrao: baixar o JWKS do emissor, achar a chave pelo `kid` do
cabecalho, conferir a assinatura RS256 e entao `aud`, `iss` e `exp`. O Entra e um
emissor como outro qualquer nessa conta, e por isso os enderecos vem de
configuracao (`config.jwks_url`, `config.issuer`). Trocar o provedor — por um
falso, em desenvolvimento — e trocar duas variaveis de ambiente.

## As quatro recusas, e por que cada uma existe

  assinatura   qualquer um consegue escrever um JWT; so quem tem a chave privada
               do emissor consegue assinar um.
  `iss`        assinatura valida de OUTRO emissor e credencial de outro mundo.
  `aud`        um token legitimo do MESMO tenant, emitido para outra aplicacao,
               tem assinatura e emissor validos. Sem conferir `aud`, qualquer
               aplicacao do tenant vira porta de entrada para esta.
  `exp`        sessao encerrada tem de encerrar o acesso.

Nenhuma delas diz ao cliente qual falhou: `TokenInvalido` sobe sem detalhe, e a
API responde sempre "Sessao invalida ou expirada". Distinguir ajudaria mais quem
esta sondando do que quem esta logando.

## O algoritmo e uma lista fechada

`algorithms=["RS256"]` nao e default nem sugestao. Um verificador que aceita o
algoritmo declarado NO PROPRIO TOKEN aceita `alg: none` e aceita HS256 assinado
com a chave publica como se fosse segredo — as duas confusoes classicas de JWT.
"""

import time
from typing import Any

import httpx
import jwt

from app.config import config

#: De onde sai o login quando `ENTRA_CLAIM_LOGIN` nao fixa um claim. A ordem e
#: deliberada: o Entra v2 costuma mandar `preferred_username`, o v1 manda `upn`, e
#: `sub` fica por ultimo porque e opaco e POR APLICACAO — serve para provar
#: identidade, nao para ser o e-mail que aparece na trilha de auditoria.
_CLAIMS_DE_LOGIN = ("preferred_username", "upn", "email", "unique_name", "sub")

#: Uma hora. As chaves do Entra giram em semanas, e um cache curto so acrescenta
#: latencia e chamadas a um endereco externo em todo request.
_TTL_JWKS = 3600.0

#: Piso entre duas buscas fora de hora. `kid` desconhecido pode ser rotacao de
#: chave (busque de novo) ou token forjado (nao busque nada) — e do lado de fora
#: as duas sao iguais. Sem o piso, um atacante repetindo `kid` aleatorio nos faria
#: martelar o emissor, transformando nosso servico no vetor.
_ESPERA_ENTRE_BUSCAS = 60.0


class TokenInvalido(Exception):
    """A credencial nao vale. O motivo fica no log, nunca na resposta."""


_chaves: dict[str, Any] = {}
_baixado_em: float = 0.0


def limpar_cache() -> None:
    """Zera o cache de chaves. Existe para os testes partirem de um estado limpo."""
    global _chaves, _baixado_em
    _chaves = {}
    _baixado_em = 0.0


async def _buscar_jwks() -> None:
    global _chaves, _baixado_em
    url = config().jwks_url
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            resposta = await cliente.get(url)
            resposta.raise_for_status()
            documento = resposta.json()
    except Exception as e:  # noqa: BLE001
        raise TokenInvalido(f"nao foi possivel obter o JWKS em {url}: {e}") from e

    novas: dict[str, Any] = {}
    for chave in documento.get("keys", []):
        kid = chave.get("kid")
        # So RS256. Uma chave de outro tipo no documento nao e erro do emissor —
        # ele pode publicar chaves para varios usos —, e sim algo que nao nos serve.
        if not kid or chave.get("kty") != "RSA":
            continue
        try:
            novas[kid] = jwt.PyJWK(chave, algorithm="RS256").key
        except Exception:  # noqa: BLE001, S112
            continue

    if not novas:
        raise TokenInvalido(f"o JWKS em {url} nao trouxe nenhuma chave RSA utilizavel")

    _chaves = novas
    _baixado_em = time.monotonic()


async def _chave_de(kid: str) -> Any:
    """A chave publica daquele `kid`, buscando o JWKS quando preciso."""
    vencido = (time.monotonic() - _baixado_em) > _TTL_JWKS
    if not _chaves or vencido:
        await _buscar_jwks()
    if kid in _chaves:
        return _chaves[kid]

    # Desconhecido com cache fresco: pode ser rotacao antes da hora. Uma busca a
    # mais, no maximo a cada `_ESPERA_ENTRE_BUSCAS`.
    if (time.monotonic() - _baixado_em) > _ESPERA_ENTRE_BUSCAS:
        await _buscar_jwks()
    if kid not in _chaves:
        raise TokenInvalido(f"kid {kid!r} nao esta no JWKS do emissor")
    return _chaves[kid]


def _login_de(reivindicacoes: dict[str, Any]) -> str:
    cfg = config()
    if cfg.entra_claim_login:
        valor = str(reivindicacoes.get(cfg.entra_claim_login) or "").strip()
        if not valor:
            raise TokenInvalido(f"o token nao traz o claim {cfg.entra_claim_login!r}")
        return valor

    for claim in _CLAIMS_DE_LOGIN:
        valor = str(reivindicacoes.get(claim) or "").strip()
        if valor:
            return valor
    raise TokenInvalido(
        "o token nao traz nenhum claim de login conhecido "
        f"(procurados: {', '.join(_CLAIMS_DE_LOGIN)})"
    )


async def login_do_token(token: str) -> str:
    """Valida o token e devolve o login. Levanta `TokenInvalido` em qualquer falha."""
    cfg = config()
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except Exception as e:  # noqa: BLE001
        raise TokenInvalido(f"cabecalho do token ilegivel: {e}") from e
    if not kid:
        raise TokenInvalido("o token nao declara `kid`, entao nao ha como escolher a chave")

    chave = await _chave_de(kid)
    try:
        reivindicacoes = jwt.decode(
            token,
            chave,
            algorithms=["RS256"],
            audience=cfg.entra_audience,
            issuer=cfg.issuer,
            # `exp` EXIGIDO, e nao apenas conferido quando presente: PyJWT trata
            # token sem `exp` como token que nao expira, e uma credencial eterna
            # emitida por engano nao teria como ser revogada pelo relogio.
            options={"require": ["exp", "aud", "iss"]},
        )
    except jwt.PyJWTError as e:
        raise TokenInvalido(str(e)) from e

    return _login_de(reivindicacoes)
