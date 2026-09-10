# Quem diz em que sistema a CTS está é a topologia, não o pareamento

`input.subbacia_cts` existe e significa SOBREPOSIÇÃO DE ÁREA: a CTS cobre um
pedaço que também é da sub-bacia. Nunca significou pertencimento, e por muito
tempo o produto o tratou como se significasse — era por ele que o servidor
decidia de que unidade a CTS era, e isso errava de duas formas: CTS sem par não
pertencia a unidade nenhuma, e CTS pareada herdava a unidade da irmã mesmo
estando num sistema de outra. Hoje a leitura é pela topologia
(`input.sistema_topologia`), e a sobreposição virou dado da própria sub-bacia,
nas colunas `*_com_cts`.

## Consequences

Uma CTS livre não é de unidade nenhuma: `GET /unidades/{u}/cts` serve só as
colocadas, e as livres chegam por `semSistema` na hierarquia. Colocar uma CTS num
sistema é a única edição de topologia que a Regional faz — todo o resto vem do
Databricks.

Quantas CTS cabem num sistema é regra de cadastro, e não do motor: para ele uma
ou duas são nós como quaisquer outros.

A caixa que decide isso era POR SISTEMA quando esta ADR foi escrita — "este
sistema usa sistema de CTS". Mudou em 09/2026 e hoje é **por unidade**, chamada
`usa_macrorregiao_cts`: marcada, cada sistema da unidade aceita UMA CTS;
desmarcada, aceitam várias. Duas razões, e as duas continuam valendo para quem
for mexer nisso: "este sistema usa CTS?" é uma pergunta que ninguém tinha como
responder 997 vezes, e o nome antigo dizia "sistema" com dois sentidos na mesma
frase — o regime e o conjunto de sub-bacias que escoam para a mesma ETE.
