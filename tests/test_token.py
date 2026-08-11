"""O que este teste protege: nenhuma credencial que nao seja legitima entra.

Cada caso e uma forma de tentar passar. Todos rodam sem rede e sem provedor de
identidade: o par de chaves nasce aqui, o JWKS e servido de um dicionario. Por
isso a mesma suite vale contra um IdP falso em desenvolvimento e contra o Entra
em producao — o que se verifica e o FORMATO, nao o fornecedor.

O caso que mais importa e `test_token_assinado_por_outra_chave_nao_passa`: e o
ataque de verdade. Os demais cobrem confusao de configuracao, que e como uma
brecha costuma nascer sem ninguem querer.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Config
from app.infra import tokens

EMISSOR = "https://idp.de-mentira/otimizador"
AUDIENCIA = "otimizador-api"
KID = "chave-1"
OUTRO_KID = "chave-2"

# Geradas UMA vez: 2048 bits custam ~100ms cada, e a suite tem quinze casos.
PRIVADA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OUTRA_PRIVADA = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(*pares) -> dict:
    """O documento JWKS como o emissor o publicaria."""
    return {
        "keys": [
            {
                **jwt.algorithms.RSAAlgorithm.to_jwk(p.public_key(), as_dict=True),
                "kid": kid,
                "use": "sig",
                "alg": "RS256",
            }
            for kid, p in pares
        ]
    }


def _assinar(reivindicacoes: dict, *, chave=PRIVADA, kid=KID) -> str:
    return jwt.encode(reivindicacoes, chave, algorithm="RS256", headers={"kid": kid})


def _validas(**extra) -> dict:
    agora = int(time.time())
    return {
        "iss": EMISSOR,
        "aud": AUDIENCIA,
        "exp": agora + 600,
        "iat": agora,
        "preferred_username": "ana@aegea.com.br",
        **extra,
    }


def _validar(token: str) -> str:
    return asyncio.run(tokens.login_do_token(token))


@pytest.fixture(autouse=True)
def idp(monkeypatch):
    """Aponta o servico para o emissor de mentira e serve o JWKS sem rede."""
    cfg = Config(
        postgres_url="postgresql://t:t@localhost:5432/t",
        service_bus_conn="",
        entra_audience=AUDIENCIA,
        entra_jwks_url="http://idp.de-mentira/jwks",
        entra_issuer=EMISSOR,
    )
    monkeypatch.setattr(tokens, "config", lambda: cfg)
    tokens.limpar_cache()

    servido = {"documento": _jwks((KID, PRIVADA)), "buscas": 0}

    async def buscar():
        # Faz o que `_buscar_jwks` faz, menos o GET: converte o documento em
        # chaves e carimba o cache. O que muda e a origem do JSON.
        servido["buscas"] += 1
        tokens._chaves = {
            c["kid"]: jwt.PyJWK(c, algorithm="RS256").key for c in servido["documento"]["keys"]
        }
        tokens._baixado_em = time.monotonic()

    monkeypatch.setattr(tokens, "_buscar_jwks", buscar)
    yield servido
    tokens.limpar_cache()


def test_token_legitimo_devolve_o_login():
    assert _validar(_assinar(_validas())) == "ana@aegea.com.br"


def test_token_assinado_por_outra_chave_nao_passa():
    """O ataque de verdade: JWT bem formado, claims certos, assinatura de outrem.

    Qualquer pessoa escreve um JWT com o conteudo que quiser. O que ela nao tem e
    a chave privada do emissor — e e so isso que separa uma credencial de um
    texto qualquer.
    """
    with pytest.raises(tokens.TokenInvalido):
        _validar(_assinar(_validas(), chave=OUTRA_PRIVADA))


def test_token_de_outra_aplicacao_do_mesmo_emissor_nao_passa():
    """Assinatura boa, emissor certo, `aud` de outro app: continua sendo de outro app."""
    with pytest.raises(tokens.TokenInvalido):
        _validar(_assinar(_validas(aud="outro-servico")))


def test_token_de_outro_emissor_nao_passa():
    with pytest.raises(tokens.TokenInvalido):
        _validar(_assinar(_validas(iss="https://idp.de-outro/x")))


def test_token_expirado_nao_passa():
    agora = int(time.time())
    with pytest.raises(tokens.TokenInvalido):
        _validar(_assinar(_validas(exp=agora - 1, iat=agora - 600)))


def test_token_sem_exp_nao_passa():
    """Sem `exp` o PyJWT trata como token eterno — e credencial eterna nao se revoga."""
    reivindicacoes = _validas()
    del reivindicacoes["exp"]
    with pytest.raises(tokens.TokenInvalido):
        _validar(_assinar(reivindicacoes))


def test_alg_none_nao_passa():
    """A confusao classica: o token DECLARA que nao esta assinado."""
    token = jwt.encode(_validas(), key="", algorithm="none", headers={"kid": KID})
    with pytest.raises(tokens.TokenInvalido):
        _validar(token)


def test_hs256_com_a_chave_publica_como_segredo_nao_passa():
    """A outra confusao classica: chave PUBLICA usada como segredo simetrico.

    Ela e publica — o atacante a tem. Um verificador que aceitasse o algoritmo
    declarado NO TOKEN aceitaria isto como assinatura valida.

    Forjado na mao de proposito: o PyJWT se recusa a EMITIR um HS256 com chave
    PEM, e quem monta este ataque nao usa PyJWT. Testar com a biblioteca educada
    testaria a educacao dela, e nao a nossa verificacao.
    """
    publica = PRIVADA.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")  # noqa: E731
    cabecalho = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    corpo = b64(json.dumps(_validas()).encode())
    assinado = cabecalho + b"." + corpo
    assinatura = b64(hmac.new(publica, assinado, hashlib.sha256).digest())
    token = (assinado + b"." + assinatura).decode()

    with pytest.raises(tokens.TokenInvalido):
        _validar(token)


def test_kid_desconhecido_nao_passa():
    with pytest.raises(tokens.TokenInvalido):
        _validar(_assinar(_validas(), kid="nunca-existiu"))


def test_chave_rotacionada_e_buscada_de_novo(idp):
    """`kid` novo com cache quente dispara UMA busca, e passa a valer."""
    _validar(_assinar(_validas()))
    buscas_antes = idp["buscas"]

    idp["documento"] = _jwks((KID, PRIVADA), (OUTRO_KID, OUTRA_PRIVADA))
    tokens._baixado_em = time.monotonic() - (tokens._ESPERA_ENTRE_BUSCAS + 1)

    assert _validar(_assinar(_validas(), chave=OUTRA_PRIVADA, kid=OUTRO_KID)) == "ana@aegea.com.br"
    assert idp["buscas"] == buscas_antes + 1


def test_kid_desconhecido_nao_martela_o_emissor(idp):
    """Repetir `kid` invalido nao pode virar uma busca ao emissor por request."""
    _validar(_assinar(_validas()))
    buscas_antes = idp["buscas"]

    for _ in range(5):
        with pytest.raises(tokens.TokenInvalido):
            _validar(_assinar(_validas(), kid="nunca-existiu"))

    assert idp["buscas"] == buscas_antes, "cache fresco nao deve gerar busca nova"


def test_o_login_sai_da_cadeia_de_claims():
    """Entra v1 manda `upn` onde o v2 manda `preferred_username`."""
    reivindicacoes = _validas()
    del reivindicacoes["preferred_username"]
    reivindicacoes["upn"] = "ana@aegea.com.br"
    assert _validar(_assinar(reivindicacoes)) == "ana@aegea.com.br"


def test_claim_fixado_na_config_nao_cai_para_a_cadeia(monkeypatch):
    """Quem fixa `ENTRA_CLAIM_LOGIN` quer AQUELE claim, e a ausencia dele e erro.

    Cair para `preferred_username` aqui esconderia que o tenant parou de emitir o
    claim configurado — e a trilha passaria a creditar um identificador diferente
    sem ninguem notar.
    """
    cfg = Config(
        postgres_url="postgresql://t:t@localhost:5432/t",
        service_bus_conn="",
        entra_audience=AUDIENCIA,
        entra_jwks_url="http://idp.de-mentira/jwks",
        entra_issuer=EMISSOR,
        entra_claim_login="upn",
    )
    monkeypatch.setattr(tokens, "config", lambda: cfg)

    assert _validar(_assinar(_validas(upn="bruno@aegea.com.br"))) == "bruno@aegea.com.br"
    with pytest.raises(tokens.TokenInvalido):
        _validar(_assinar(_validas()))  # so tem preferred_username


def test_sem_nenhum_claim_de_login_nao_passa():
    """Token valido e anonimo nao serve: a trilha de auditoria precisa de um nome."""
    reivindicacoes = _validas()
    del reivindicacoes["preferred_username"]
    with pytest.raises(tokens.TokenInvalido):
        _validar(_assinar(reivindicacoes))
