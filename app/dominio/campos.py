"""QUAIS CAMPOS EXISTEM, e de quem é cada um.

A régua que decide o que a tela trava (veio do Databricks) e o que a Regional
preenche é a MESMA que decide como a trilha nomeia a mudança. Enquanto ela morava
no repositório de leitura e a trilha morava no de escrita, nada impedia as duas de
divergirem — e divergindo, a trilha chamaria de correção o que a tela nem oferece
corrigir.

A cardinalidade das obras vinha de `pendencias`, pelo mesmo motivo pelo qual
agora vem daqui: é a régua que o `/prontidao` usa para denunciar obra ausente, e
duas cópias dela fariam a tela dizer que a ficha está incompleta e o `PUT`
aceitá-la — ou o contrário, que é pior.
"""


#: Quais campos vêm do Databricks (travados, corrigíveis só por override) e quais
#: a Regional preenche. A divisão é a do `DEPLOY.md` §3.
#:
#: O RECORTE RESIDENCIAL (`ligURes`, `ligARes`, `ecoURes`, `ecoARes`) é medida do
#: Databricks como as do topo — é a parcela residencial APURADA na base comercial, e
#: não uma estimativa de quem cadastra. Cair em `params` faria a tela pedir como
#: preenchimento o que é dado travado, e a Regional digitaria por cima sem gerar
#: trilha de override.
DO_DATABRICKS = {
    "fat",
    "arr",
    "ligU",
    "ligA",
    "ligN",
    "ecoU",
    "ecoA",
    "ecoN",
    "ligURes",
    "ligARes",
    "ecoURes",
    "ecoARes",
}


#: O que a ficha de coleta DEVE trazer em cada bloco. É o contrato do front
#: (`SubBaciaDb` / `SubBaciaParams`), e é o que torna o PUT uma substituição de
#: ficha inteira em vez de um patch — ver `dominio.ficha.exigir_ficha_inteira`.
#: `ticket` fica de FORA: ele e derivado (receita/ligacoes), nao tem coluna, e o
#: PUT nao o grava. Exigi-lo no corpo obrigaria o cliente a devolver uma conta que
#: o servidor mesmo fez.
CAMPOS_DB = sorted(DO_DATABRICKS)


CAMPOS_PARAMS = ["preco", "tarr", "ramp", "vaz", "pot", "popU", "popA"]


#: `popN` (`populacao_novas_obras`) existe na tabela e NÃO é modelado pelo front:
#: não está em `SubBaciaDb` nem em `SubBaciaParams`. Por isso a escrita nunca o
#: toca — zerá-lo em nome de "ficha inteira" apagaria uma coluna que o cliente
#: nem sabe que existe.
NAO_MODELADOS = {"popN"}


#: Campos de obra que a simulação exige. `wacc` fora, de propósito.
#: Quantas obras cada ficha TEM DE ter. E a base do cadastro (5 para sub-bacia,
#: 4 para CTS), e o que permite contar a obra AUSENTE — que nao aparece em
#: `componentes_*_capex` e por isso passava despercebida.
OBRAS_SUBBACIA = 5


OBRAS_CTS = 4


#: Colunas da ficha de coleta -> nomes do front. Sub-bacia e CTS são idênticas:
#: a mesma ficha, duas tabelas. Um dicionário só evita as duas divergirem.
COLETA = {
    "preco_por_ligacao": "preco",
    "receita_faturada_media_mensal": "fat",
    "receita_arrecadada_media_mensal": "arr",
    "tempo_arrecadacao": "tarr",
    "tempo_ramp_up": "ramp",
    "vazao_contribuicao": "vaz",
    "universo_ligacoes": "ligU",
    "ligacoes_atuais": "ligA",
    "ligacoes_novas_obras": "ligN",
    "universo_economias": "ecoU",
    "economias_atuais": "ecoA",
    "economias_novas_obras": "ecoN",
    "universo_populacao": "popU",
    "populacao_atual": "popA",
    "populacao_novas_obras": "popN",
    "potencial_crescimento": "pot",
    "universo_ligacoes_residencial": "ligURes",
    "ligacoes_atuais_residencial": "ligARes",
    "universo_economias_residencial": "ecoURes",
    "economias_atuais_residencial": "ecoARes",
}
