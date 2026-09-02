"""As formas dos payloads de CADASTRO, SIMULAÇÃO e SAÚDE.

Companheiro de `formas_resultado.py` — a motivação e o arranjo estão lá, e valem
igual aqui: `response_model=` no decorador, repositório devolvendo o dicionário
cru, e `| None` como afirmação e não descuido.

## Por que quase tudo é `str`

Não é preguiça de tipar. A tela de cadastro trata TODA célula como string
editável e chama `.trim()` nela; um número aqui viraria `null` no campo vazio, e
`null.trim()` derruba a tela inteira, não só o campo (ver `_auditoria`, em
`repositorios/cadastro.py`). O contrato é string, e o modelo diz isso.

## O que fica aberto, e por quê

`db`, `params` e `obrasOverride` da ficha de coleta são mapas de chave curta para
valor — as chaves são as colunas daquela aba, e o de/para vive no front
(`lib/cadastroApi.ts`). Declará-las aqui criaria uma segunda definição, para
envelhecer em ritmo diferente da primeira.
"""

from pydantic import BaseModel


# ===========================================================================
#  Organização — regionais e unidades
# ===========================================================================
class Regional(BaseModel):
    id: str
    nome: str


class Diretoria(BaseModel):
    """O nível entre a regional e a unidade.

    `nome` é `| None` porque a carga pode trazer a diretoria sem nome — mesma
    tolerância de `regional_name`.
    """

    id: str
    nome: str | None


class ResumoDaUnidade(BaseModel):
    cidades: int
    sistemas: int
    subBacias: int
    etes: int
    cts: int
    obras: int
    obrasAegea: int
    obrasTerceiros: int
    semObra: int


class Unidade(BaseModel):
    id: str
    nome: str
    regionalId: str
    #: `| None` enquanto a carga não trouxer a diretoria da unidade. A tela cai
    #: para "sem diretoria" em vez de esconder a unidade.
    diretoriaId: str | None
    diretoriaNome: str | None
    waccMedio: float | None
    completude: int
    #: Falso quando a carga do Databricks não chegou — a tela avisa em vez de
    #: mostrar cadastro pela metade como se fosse o que existe.
    databricksConectado: bool
    resumo: ResumoDaUnidade


# ===========================================================================
#  Hierarquia
# ===========================================================================
class UnidadeERegional(BaseModel):
    rid: str
    rnome: str
    #: A diretoria, entre a regional e a unidade. Vazio enquanto a carga não a
    #: trouxer — `""`, e não nulo, como todo campo do Grupo 01.
    did: str
    dnome: str
    uid: str
    unome: str
    waccMedio: str
    #: `'true'`/`'false'` minusculo, como todo booleano do Grupo 01. Marcada, cada
    #: sistema da unidade aceita UMA CTS.
    usaCts: str


class Empresa(BaseModel):
    """O nível entre a unidade e o município (modelo de dados v8).

    Chamava-se superintendência até a v7. A troca é de nome e de posição no
    caminho — `regional > diretoria > unidade > empresa > cidade` —, não de
    conteúdo: o
    campo continua sendo (código, nome) e a tabela de origem manteve as três
    colunas que tinha.
    """

    id: str
    nome: str
    #: Ano do fim da concessao DA EMPRESA — a fonte de verdade do prazo.
    #:
    #: Vazio enquanto a Aegea nao informar, e nesse caso cada municipio mantem o
    #: ano que ja tinha. Preenchido, ele desce para todas as cidades da empresa
    #: (gatilho `empresa_propaga_concessao`), porque e por cidade que o motor le.
    #:
    #: Vem como texto porque o Grupo 01 inteiro e string (ver `hierarquia`).
    fimConcessao: str


class CidadeDaHierarquia(BaseModel):
    id: str
    nome: str
    empId: str


class SistemaDaHierarquia(BaseModel):
    id: str
    nome: str
    cidId: str


class NoDaTopologia(BaseModel):
    sis: str
    id: str
    nome: str
    jus: str
    tipo: str


class ComponenteSemSistema(BaseModel):
    """Componente fora de qualquer sistema — hoje, as CTS ainda não colocadas.

    Elas NÃO têm ficha em `cts-operacional`: aquela rota serve as CTS da unidade,
    e uma CTS livre não é de unidade nenhuma. É por aqui que a tela sabe que ela
    existe, e é `tipo` que diz o que ela é.
    """

    id: str
    nome: str
    tipo: str


class Hierarquia(BaseModel):
    unidReg: UnidadeERegional
    empresas: list[Empresa]
    cidades: list[CidadeDaHierarquia]
    sistemas: list[SistemaDaHierarquia]
    topo: list[NoDaTopologia]
    semSistema: list[ComponenteSemSistema]


# ===========================================================================
#  Contrato
# ===========================================================================
class CidadeDoContrato(BaseModel):
    id: str
    nome: str
    #: A empresa que responde pelo município — o nível acima dele na hierarquia.
    #: A aba mostrava as duas colunas vazias porque a consulta não as trazia.
    empId: str
    empNome: str
    #: Fim da concessão. LEITURA: quem o define é a empresa, e o banco o desce
    #: para as cidades dela. Continua no payload porque a régua de cobertura
    #: divide a ficha com ele.
    fim: str
    cob: str
    atualizadoEm: str
    atualizadoPor: str


class MetaDoContrato(BaseModel):
    cid: str
    ano: str
    pct: str


class FaixaDeFatorEsgoto(BaseModel):
    cid: str
    cob: str
    par: str


class Contrato(BaseModel):
    cidades: list[CidadeDoContrato]
    metas: list[MetaDoContrato]
    fator: list[FaixaDeFatorEsgoto]


# ===========================================================================
#  Fichas de coleta — sub-bacia e CTS têm a MESMA forma
# ===========================================================================
class FichaDeColeta(BaseModel):
    id: str
    nome: str
    sisId: str
    sistema: str
    jusante: str
    #: Veio do Databricks, travado na tela.
    db: dict[str, str]
    #: A Regional preenche.
    params: dict[str, str]
    #: Índice da obra (posição na ordem canônica) → colunas sobrescritas.
    obrasOverride: dict[str, dict[str, str]]
    atualizadoEm: str
    atualizadoPor: str


class SistemaDaArvore(BaseModel):
    id: str
    nome: str
    subIds: list[str]


class CidadeDaArvore(BaseModel):
    id: str
    nome: str
    sistemas: list[SistemaDaArvore]


class RamoDaArvore(BaseModel):
    id: str
    nome: str
    cidades: list[CidadeDaArvore]


class SubBacias(BaseModel):
    arvore: list[RamoDaArvore]
    subs: dict[str, FichaDeColeta]


class Inconsistencia(BaseModel):
    """Cadastro meio existente — componente colocado no sistema sem ficha.

    `{tipo, id, detalhe}` é a forma com que esta base denuncia isso, e a mesma
    que `prontidao.faltando` usa.
    """

    tipo: str
    id: str
    detalhe: str


class Cts(BaseModel):
    ctss: dict[str, FichaDeColeta]
    inconsistencias: list[Inconsistencia]


class Ete(BaseModel):
    id: str
    cidId: str
    #: O SISTEMA da ETE. A consulta já passava por `cidade_sistema` para achar a
    #: unidade; faltava trazer a coluna, e a tela mostrava "ID Sistema" vazio.
    sisId: str
    sistema: str
    sub: str
    capMod: str
    capexMod: str
    opexMod: str
    tPred: str
    tExec: str
    anoObrig: str
    proibAte: str
    capNom: str
    vazOp: str
    nova: str
    terreno: str
    modulos: str
    wacc: str
    atualizadoEm: str
    atualizadoPor: str


class Etes(BaseModel):
    etes: list[Ete]


# ===========================================================================
#  Trilha e gravação
# ===========================================================================
class Alteracao(BaseModel):
    """Uma linha da trilha de override.

    `de` e `para` sao NULOS quando o campo estava vazio ou foi esvaziado —
    apagar um valor e uma alteracao como outra qualquer, e a trilha a registra
    com o lado correspondente em branco. Estava tipado `str` aqui, e a primeira
    limpeza de campo derrubou `GET /alteracoes` com 500.
    """

    quando: str
    autor: str
    origem: str
    tipo: str
    fichaId: str
    campo: str
    de: str | None
    para: str | None


class Alteracoes(BaseModel):
    alteracoes: list[Alteracao]
    #: Verdadeiro quando a lista foi truncada — a tela diz que há mais.
    cortado: bool


class Gravacao(BaseModel):
    """O retorno de toda escrita de cadastro.

    `alteracoesGravadas` é 0 quando nada mudou de fato, e isso NÃO é erro: gravar
    a mesma ficha duas vezes é idempotente, e a trilha não registra repetição.

    `atualizadoEm`/`atualizadoPor` só voltam nas fichas que carregam carimbo de
    autoria — topologia e sistema não têm.
    """

    id: str
    alteracoesGravadas: int
    atualizadoEm: str | None = None
    atualizadoPor: str | None = None


# ===========================================================================
#  Simulação
# ===========================================================================
class Prontidao(BaseModel):
    unidadeId: str
    unidadeNome: str
    pendencias: int
    porGrupo: dict[str, int]
    faltando: list[Inconsistencia]


class RodadaCriada(BaseModel):
    runId: str
    status: str
    #: Disparo repetido devolve a rodada em voo em vez de abrir outra igual.
    jaExistia: bool


class VariacaoCriada(RodadaCriada):
    """A resposta de `POST /runs/{id}/variacao`.

    Modelo proprio, e nao `RodadaCriada` com um campo a mais: `naCurva` so faz
    sentido para uma variacao, e po-lo em `POST /runs` obrigaria aquela rota a
    responder algo verdadeiro sobre uma curva que ela nao conhece.
    """

    #: `False` quando a rodada devolvida ja e ponto da curva de OUTRA base — ela
    #: existe e o resultado e valido, mas nao vai aparecer neste grafico.
    naCurva: bool


class RodadaAceita(BaseModel):
    runId: str
    status: str


class FilaDaRodada(BaseModel):
    """POR QUE esta rodada está onde está — só enquanto ela está em voo.

    "Na fila, esperando um executor" cobria dois mundos opostos: fila cheia com
    executor trabalhando, e NENHUM executor no ar. `atencao` separa os dois.
    """

    vivos: int
    capacidade: int
    ocupadas: int
    posicao: int
    motivo: str
    #: Verdadeiro quando não é fila normal — ninguém tirando da fila, ou lease
    #: vencido. A tela destaca.
    atencao: bool


class StatusDaRodada(BaseModel):
    runId: str
    status: str
    #: `int`, e não `int | None`: o endpoint faz `progresso or 0`, então nunca
    #: manda nulo. Declarar opcional publicaria no OpenAPI um estado que a
    #: implementação não produz.
    progresso: int
    pedidaEm: str | None
    erro: str | None
    #: AUSENTE quando a rodada terminou — só PENDENTE e RODANDO têm fila. Por
    #: isso a rota usa `response_model_exclude_unset`: com default `None` o
    #: campo viraria `"fila": null` numa rodada concluída, e antes a chave nem
    #: existia.
    fila: FilaDaRodada | None = None


# ===========================================================================
#  Saúde — fora do /api, e sem recorte de unidade
# ===========================================================================
class Saude(BaseModel):
    status: str


class ProntidaoDoServico(BaseModel):
    banco: bool
    migracoesFaltando: list[str]
    filaConfigurada: bool
    fila: bool
    autenticacao: str
    ambiente: str
