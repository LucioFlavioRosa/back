"""Dependencias compartilhadas pelos endpoints.

A que carrega peso e a IDENTIDADE. Ela nao e enfeite de auditoria: decide QUEM
ASSINA cada simulacao — o valor vai para `controle.run_request.solicitado_por` e,
dali, para `otim_meta.usuario` — e, desde que o acesso passou a ser por usuario,
decide tambem O QUE A PESSOA VE.

Duas perguntas diferentes, e vale nao confundi-las:

  ESCOPO   quais unidades a pessoa acessa (tabela `controle.usuario_acesso`).
           Responde "esta unidade e sua?" — vale para cadastro e simulacao.
  POSSE    de quem e uma rodada especifica. Responde "este resultado e seu?".
           Uma pessoa com acesso a unidade NAO ve automaticamente as rodadas dos
           colegas dela: cada um ve as proprias, e `admin` ve todas.

Por isso o login sai do TOKEN e nunca do corpo. Aceitar do corpo seria aceitar
que qualquer um assinasse — e agora tambem LESSE — o trabalho de outro.

Enquanto o provedor de identidade nao esta configurado (`config.exige_auth`
falso), o servico roda sem exigir token e usa um usuario de desenvolvimento. O
`/readyz` denuncia esse modo para que ele nao chegue a producao sem que alguem
veja. Com ele configurado, todo request precisa de `Authorization: Bearer` e o
token e validado em `app.infra.tokens`.
"""

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import config

USUARIO_DEV = "dev@local"

#: So vale com a autenticacao DESLIGADA. Ver `identidade_atual`.
CABECALHO_DEV = "x-usuario-dev"

#: O unico papel que o codigo entende hoje. Os outros sao guardados na tabela e
#: ignorados aqui — de proposito: o conjunto de papeis ainda nao esta fechado, e a
#: tabela precisa poder crescer antes do codigo.
ADMIN = "admin"


@dataclass(frozen=True)
class Identidade:
    """Quem esta pedindo, o que pode fazer, e sobre o que."""

    login: str
    papeis: frozenset[str] = frozenset()
    #: Unidades concedidas — ja com as regionais expandidas nas unidades delas.
    unidades: frozenset[str] = frozenset()
    #: Escopo total (uma concessao sem regional nem unidade, com papel `admin`).
    tudo: bool = False

    @property
    def admin(self) -> bool:
        return ADMIN in self.papeis

    def acessa_unidade(self, unidade_id: str) -> bool:
        return self.tudo or unidade_id in self.unidades

    def ve_rodada_de(self, dono: str | None) -> bool:
        """A POSSE de uma rodada, que e mais estreita que o escopo.

        `dono` ausente e SO DO ADMIN, e nao de todo mundo: `otim_meta.usuario` aceita
        NULL, entao bastava um script publicar sem autor para a rodada ficar
        legivel por qualquer um que soubesse o `run_id`. Uma brecha que se abre
        sozinha, por descuido de carga, e pior que uma que exige ataque.

        Hoje nao ha rodada sem dono (medido: 0 de 13). Se aparecer, o admin ve e
        pode atribuir.
        """
        if self.admin:
            return True
        return bool(dono) and dono.lower() == self.login.lower()


async def identidade_atual(
    authorization: Annotated[str | None, Header()] = None,
    x_usuario_dev: Annotated[str | None, Header()] = None,
) -> Identidade:
    cfg = config()

    if not cfg.exige_auth:
        # SEM AUTENTICACAO, `X-Usuario-Dev` troca de usuario. Existe para dar para
        # exercitar o recorte em ambiente local, onde todo mundo seria `dev@local`
        # e nem privacidade nem escopo apareceriam.
        #
        # Ele e ignorado assim que a autenticacao liga — nao ha caminho em que ele
        # valha junto com token. Se valesse, seria o buraco mais obvio possivel:
        # um cabecalho de texto escolhendo a identidade.
        login = (x_usuario_dev or USUARIO_DEV).strip() or USUARIO_DEV
    else:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão inválida ou expirada.")
        token = authorization.split(" ", 1)[1].strip()
        try:
            login = await _identidade_do_token(token)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Sessão inválida ou expirada."
            ) from e

    return await _com_acesso(login)


async def _com_acesso(login: str) -> Identidade:
    """Monta a identidade a partir das concessoes da tabela.

    SEM CONCESSAO = SEM ACESSO. Falha fechada de proposito: o modo de falha errado
    aqui e o permissivo, porque ele nao aparece — tudo funciona, para todo mundo,
    ate o dia em que aparece do pior jeito.
    """
    from app.infra.repositorios import controle

    linhas = await controle.acesso(login)
    papeis = {str(l["papel"]).strip().lower() for l in linhas if l.get("papel")}

    unidades: set[str] = set()
    tudo = False
    for l in linhas:
        if l.get("unidade_id"):
            unidades.add(l["unidade_id"])
        elif l.get("regional_id"):
            unidades.update(await controle.unidades_da_regional(l["regional_id"]))
        elif ADMIN in papeis:
            # Concessao sem escopo so vale como "tudo" para quem tem papel que a
            # justifique. Uma linha sem escopo com papel qualquer nao abre o banco.
            tudo = True

    return Identidade(
        login=login,
        papeis=frozenset(papeis),
        unidades=frozenset(unidades),
        tudo=tudo,
    )


def exigir_unidade(quem: Identidade, unidade_id: str) -> None:
    """404 — e nao 403 — para unidade fora do escopo.

    403 confirmaria que a unidade existe, e "existe mas não é sua" ja e informacao:
    daria para mapear a organizacao inteira variando o id. Para quem nao tem
    acesso, a unidade simplesmente nao existe. E o mesmo criterio que
    `cadastro_escrita.exigir_dona` usa para ficha de outra unidade.
    """
    if not quem.acessa_unidade(unidade_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unidade não encontrada.")


async def exigir_rodada(quem: Identidade, run_id: str) -> None:
    """404 para rodada fora do escopo OU de outra pessoa.

    As duas condicoes, e nesta ordem — o escopo primeiro, porque ele vale para
    todo mundo. `admin` relaxa a POSSE: ve as rodadas dos colegas, mas so nas
    unidades que lhe foram concedidas. Antes ele via o banco inteiro, o que fazia
    de "papel" e "escopo" a mesma coisa e esvaziava a tabela de concessao.
    """
    from app.infra.repositorios import controle

    r = await controle.de_quem(run_id)
    unidade = (r or {}).get("unidade")
    # Unidade desconhecida (rodada antiga, sem pedido nem de-para) nao bloqueia
    # aqui: a posse abaixo ainda decide. Perder acesso a dado antigo por falta de
    # cadastro seria transformar lacuna em incidente.
    if unidade and not quem.acessa_unidade(unidade):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rodada não encontrada.")

    if not quem.ve_rodada_de((r or {}).get("dono")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rodada não encontrada.")


async def usuario_atual(quem: Annotated[Identidade, Depends(identidade_atual)]) -> str:
    """Só o login. Para quem grava autoria e não precisa decidir visibilidade."""
    return quem.login


async def _identidade_do_token(token: str) -> str:
    """Valida o access token e devolve quem e o usuario.

    A verificacao mora em `app.infra.tokens` (JWKS + RS256 + `aud`/`iss`/`exp`).
    Aqui fica so a traducao: qualquer recusa vira 401 com a MESMA mensagem, sem
    dizer o que falhou. O detalhe vai para o log — ele ajuda quem opera, e para
    quem sonda ele seria um oraculo.

    O ESCOPO (quais unidades) NAO sai do token: continua em
    `controle.usuario_acesso`, porque que unidades alguem acessa e decisao do
    negocio, nao do diretorio corporativo. O PAPEL pode passar a sair do claim
    `roles` quando o tenant estiver configurado para emiti-lo.
    """
    from app.infra.tokens import TokenInvalido, login_do_token

    try:
        return await login_do_token(token)
    except TokenInvalido as e:
        logging.getLogger(__name__).warning("token recusado: %s", e)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Sessão inválida ou expirada."
        ) from e


async def guarda_de_rota(
    request: Request, quem: Annotated[Identidade, Depends(identidade_atual)]
) -> None:
    """Aplica escopo e posse a partir dos PARAMETROS DA ROTA.

    Uma dependencia no roteador, e nao uma linha em cada endpoint, porque
    autorizacao esquecida nao falha: o endpoint funciona, so que para todo mundo.
    Sao 20 rotas hoje; bastava uma passar batida. Assim, rota nova com
    `{unidade_id}` ou `{run_id}` nasce protegida sem ninguem lembrar de nada.

    DUAS LIMITACOES, e as duas ja morderam:

    1. So enxerga PARAMETRO DE CAMINHO. `POST /runs` recebe a unidade no CORPO e
       passava batido — alguem com acesso a uma unidade disparava simulacao em
       outra e, como o disparo grava autoria, virava dono do resultado. Rota que
       recebe unidade pelo corpo precisa chamar `exigir_unidade` na mao.
    2. Depende do NOME do parametro. `/regionais/{regional_id}/unidades` nao tem
       `unidade_id` e listava as unidades de qualquer regional para qualquer um.

    Ou seja: isto reduz o numero de lugares onde da para esquecer, e nao o zera.
    O teste em `tests/test_autorizacao.py` percorre as rotas registradas e exige
    que cada uma esteja coberta pelo guarda OU declarada como recortada na mao —
    rota nova aparece la, e nao em producao.
    """
    unidade_id = request.path_params.get("unidade_id")
    if unidade_id:
        exigir_unidade(quem, unidade_id)

    run_id = request.path_params.get("run_id")
    if run_id:
        await exigir_rodada(quem, run_id)


Usuario = Annotated[str, Depends(usuario_atual)]
Quem = Annotated[Identidade, Depends(identidade_atual)]
