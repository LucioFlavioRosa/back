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

    # ---------------------------------------------------------------- fila
    service_bus_conn: str = Field(
        ...,
        description="Connection string do Service Bus. Vazio so e aceito em teste.",
    )
    fila_simulacoes: str = "otimizacoes"

    # ---------------------------------------------------------------- auth
    # Enquanto `entra_tenant_id` estiver vazio o servico NAO exige token — e o modo
    # de desenvolvimento, espelhando o front, que so manda Authorization quando o
    # SSO esta configurado no /config.js. Em producao isto e obrigatorio, e o
    # `readyz` avisa quando esta desligado.
    entra_tenant_id: str = ""
    entra_audience: str = ""

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
        return bool(self.entra_tenant_id and self.entra_audience)


@lru_cache
def config() -> Config:
    return Config()  # type: ignore[call-arg]
