"""A CAUSA DE UMA FALHA, como ela pode ir para a tela.

`controle.run_status.erro` guarda o que o executor reportou — `f"{type(e).__name__}:
{e}"` — e a lista de rodadas passou a mostrar esse texto a quem clicou. Uma mensagem
de exceção carrega o que estiver no caminho dela: a URL do banco com senha, o caminho
de um arquivo local, um pedaço de SQL. Nada disso ajuda quem lê o histórico, e a
senha não pode chegar lá.

Uma régua só, aplicada nas DUAS pontas: o worker ao gravar (`dev/worker.py`) e a API
ao servir (`repositorios/resultado.py`, `api/simulacao.py`). Nas duas porque nenhuma
delas é a única porta — o job do Databricks grava sem passar pelo worker, e um dump
antigo já tem o que já tem.
"""

import re

__all__ = ["CAUSA_MAX", "causa_segura"]

#: O worker já cortava em 500; a API corta no mesmo tamanho, para o que sai bater
#: com o que entrou.
CAUSA_MAX = 500

#: `postgresql://usuario:senha@host` -> `postgresql://***@host`. Qualquer esquema,
#: qualquer usuário: o que se esconde é o par inteiro antes do `@`.
_CREDENCIAL_EM_URL = re.compile(r"(\w+://)[^\s/@]+:[^\s/@]+@")
#: Chave=valor de segredo em connection string ou mensagem (`SharedAccessKey=...`,
#: `password=...`). O NOME fica, o valor sai — quem lê ainda sabe do que se trata.
_SEGREDO_NOMEADO = re.compile(
    r"(?i)\b(password|senha|passwd|pwd|sharedaccesskey|accountkey|token|secret|api[_-]?key)\s*=\s*[^\s;,&]+"
)
#: Caminho absoluto de arquivo (`C:\...\x.py`, `/home/.../x.py`): ruído para quem lê,
#: e mapa da máquina para quem não deveria. Fica só o nome do arquivo.
_CAMINHO = re.compile(r"(?:[A-Za-z]:\\|/)(?:[^\s\\/:\"'<>|]+[\\/])+([^\s\\/:\"'<>|]+)")


def causa_segura(texto: str | None) -> str | None:
    """A mensagem do executor sem credencial, sem caminho de máquina, no tamanho da tela.

    `None` continua `None`: ausência de causa é informação (a rodada não falhou, ou
    falhou sem dizer), e virá-la em string vazia apagaria essa diferença.
    """
    if texto is None:
        return None
    t = str(texto)
    t = _CREDENCIAL_EM_URL.sub(r"\1***@", t)
    t = _SEGREDO_NOMEADO.sub(lambda m: m.group(0).split("=", 1)[0] + "=***", t)
    t = _CAMINHO.sub(r"\1", t)
    t = " ".join(t.split())
    return t[:CAUSA_MAX]
