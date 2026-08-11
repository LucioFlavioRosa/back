"""Imprime um access token do provedor de identidade de mentira.

    python dev/token_de_teste.py            # dev@local
    python dev/token_de_teste.py ana        # ana@aegea.com.br
    python dev/token_de_teste.py ana --ver  # mostra os claims em vez do token

    curl -H "Authorization: Bearer $(python dev/token_de_teste.py ana)" \
         localhost:8000/api/unidades

Só serve com a pilha do `docker-compose.sso.yml` no ar. O usuário sai do
`client_id`, que o mock mapeia para os claims — ver o `JSON_CONFIG` lá.

O token sai POR AQUI, do host, e não de dentro da rede do Docker: o mock deriva o
`iss` do cabeçalho Host, e a API espera `http://localhost:8099/otimizador`.
Pedi-lo de dentro de um container geraria `iss` diferente, e a API o recusaria —
corretamente.
"""

import base64
import json
import sys
import urllib.parse
import urllib.request

IDP = "http://localhost:8099/otimizador/token"
ESCOPO = "otimizador-api"


def token(client_id: str) -> dict:
    corpo = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": "nao-conferido-pelo-mock",
            "scope": ESCOPO,
        }
    ).encode()
    pedido = urllib.request.Request(
        IDP, data=corpo, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(pedido, timeout=10) as r:
        return json.loads(r.read())


def claims(jwt: str) -> dict:
    corpo = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)))


if __name__ == "__main__":
    argumentos = [a for a in sys.argv[1:] if not a.startswith("-")]
    quem = argumentos[0] if argumentos else "dev"
    try:
        resposta = token(quem)
    except Exception as e:  # noqa: BLE001
        print(f"nao consegui falar com o IdP em {IDP}: {e}", file=sys.stderr)
        print("a pilha do docker-compose.sso.yml esta no ar?", file=sys.stderr)
        raise SystemExit(1)

    if "--ver" in sys.argv:
        print(json.dumps(claims(resposta["access_token"]), indent=2, ensure_ascii=False))
    else:
        print(resposta["access_token"])
