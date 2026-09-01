"""Configuracao do servico, lida do ambiente uma unica vez.

Nada de segredo em codigo: no AKS os valores vem de `Secret`/`ConfigMap`, e os
segredos de verdade (senha do Postgres, connection string do Service Bus) do Key
Vault via CSI driver, montados como variavel de ambiente.

`POSTGRES_URL` e `SERVICE_BUS_CONN` nao tem default de proposito. Um default
plausivel aqui — `localhost`, uma fila de teste — e como um servico sobe apontando
para o lugar errado e ninguem percebe ate o dado aparecer no banco errado. Sem
valor, o processo nao sobe.
"""

import re
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---------------------------------------------------------------- banco
    postgres_url: str = Field(
        ...,
        description=(
            "postgresql://user:senha@host:5432/otimizador — o MESMO formato que o job "
            "de producao usa. Nao use `postgresql+psycopg2://`: o psycopg2 do pacote "
            "de producao rejeita esse prefixo do SQLAlchemy."
        ),
    )
    schema_input: str = "input"
    schema_controle: str = "controle"
    schema_resultado: str = "public"
    pool_min: int = 2
    pool_max: int = 10
    #: Teto de UMA consulta dentro de transacao de escrita, em milissegundos.
    #:
    #: O `command_timeout` do pool cancela do lado do cliente e ja cobre a tela
    #: travada. Este e o do SERVIDOR, e existe por causa das transacoes que
    #: seguram advisory lock de varios sistemas: enquanto uma delas espera, todo
    #: mundo que grava naqueles sistemas espera junto. Abortar por conta propria
    #: devolve os locks; esperar o cliente desistir os segura ate la.
    statement_timeout_ms: int = 15_000

    # ---------------------------------------------------------------- fila
    service_bus_conn: str = Field(
        ...,
        description="Connection string do Service Bus. Vazio so e aceito em teste.",
    )
    fila_simulacoes: str = "otimizacoes"

    # ---------------------------------------------------------------- auth
    # Enquanto a auth estiver desligada o servico NAO exige token — e o modo de
    # desenvolvimento, espelhando o front, que so manda Authorization quando o SSO
    # esta configurado. Em producao isto e obrigatorio, e o `readyz` avisa quando
    # esta desligado.
    entra_tenant_id: str = ""
    entra_audience: str = ""

    # OS TRES OVERRIDES ABAIXO EXISTEM PARA APONTAR A VALIDACAO PARA OUTRO IdP.
    # A verificacao e OIDC padrao (JWKS + RS256 + aud/iss/exp), entao trocar o
    # Entra por um provedor de mentira em desenvolvimento e trocar ENDERECO, e nao
    # codigo. Vazios, os dois primeiros sao derivados do tenant.
    entra_jwks_url: str = ""
    entra_issuer: str = ""
    #: De qual claim sai o login. Vazio = tenta a cadeia `_CLAIMS_DE_LOGIN`, que
    #: cobre as formas que o Entra usa conforme a versao do token e os escopos
    #: concedidos. Preencher fixa UM claim, e a ausencia dele passa a ser erro —
    #: que e o que se quer quando ja se sabe qual o tenant emite.
    entra_claim_login: str = ""


    # ---------------------------------------------------------------- resto
    redis_url: str = ""
    ambiente: str = "desenvolvimento"
    origens_cors: list[str] = []

    @field_validator("schema_input", "schema_controle", "schema_resultado")
    @classmethod
    def _identificador(cls, v: str) -> str:
        """Os schemas entram no SQL por f-string — nao da para parametriza-los.

        Nao e explorável por HTTP (nenhum vem do usuario), mas um valor vindo de
        ConfigMap errado quebraria toda consulta com erro de sintaxe, e um valor
        hostil executaria SQL sob as credenciais do servico. Recusar no startup e
        barato e fecha a porta antes de ela existir.
        """
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError(f"nome de schema invalido: {v!r}")
        return v

    @property
    def exige_auth(self) -> bool:
        """Ha para onde validar E o que exigir no `aud`?

        A `audience` e obrigatoria nos dois casos, e nao por formalidade: sem ela
        o servico aceitaria um token legitimo emitido para OUTRA aplicacao do
        mesmo tenant — assinatura valida, emissor valido, e credencial que nunca
        foi para nos.
        """
        return bool(self.entra_audience and (self.entra_tenant_id or self.entra_jwks_url))

    @property
    def jwks_url(self) -> str:
        """Onde estao as chaves publicas que assinam o token."""
        if self.entra_jwks_url:
            return self.entra_jwks_url
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}/discovery/v2.0/keys"

    @property
    def issuer(self) -> str:
        """Quem o token tem de declarar como emissor (claim `iss`).

        O `/v2.0` no fim NAO e enfeite: o endpoint v1 do Entra emite `iss` sem
        ele, e um token v1 apresentado a um servico que espera v2 falha aqui — que
        e o comportamento certo, porque as duas versoes tambem diferem no formato
        do `aud` e no claim que carrega o login.
        """
        if self.entra_issuer:
            return self.entra_issuer
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}/v2.0"


@lru_cache
def config() -> Config:
    return Config()  # type: ignore[call-arg]
