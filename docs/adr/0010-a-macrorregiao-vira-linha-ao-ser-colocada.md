# A macrorregião de CTS vira linha quando é colocada num sistema

Uma unidade marcada como **macrorregião de CTS** agrupa seus coletores por
`(sistema_cts, emp_codigo)`, e é o grupo — não o coletor — que a tela oferece ao
montar o sistema. Faltava dizer ONDE esse grupo existe.

A decisão: ele existe como uma linha de `input.cts_operacional`, marcada com
`e_macrorregiao`, criada no instante em que a macrorregião é COLOCADA num sistema
(`_preparar_macrorregiao`). Antes disso ela não tem linha nenhuma, e a ficha que a
tela mostra é a soma calculada na leitura.

## Por que não ao marcar a unidade

Marcar é declaração de regime. Materializar ali criaria dezenas de agregados que
talvez ninguém use, e desmarcar teria de apagá-los — com as obras já preenchidas
dentro. Colocar é o ato que diz "esta macrorregião entra no cadastro", e é o
primeiro momento em que a linha é NECESSÁRIA: `componentes_cts_capex.cts` é FK
para `cts_operacional(cts)`, e o motor lê a ficha do nó na própria tabela.

## Por que uma coluna, e não deduzir pelo nome

Sem `e_macrorregiao`, "esta linha é uma macrorregião?" só se responde cruzando
`cts` com os `sistema_cts` das outras — identidade por coincidência de nome, que
passa a errar no dia em que a origem batizar um coletor com o nome de um grupo.
A coluna também fecha o estado impossível: a CHECK `macrorregiao_nao_e_membro`
impede que uma linha seja agregado e membro ao mesmo tempo, o que faria a soma se
incluir.

## Consequences

**A ficha tem uma verdade por estado, e elas se revezam.** Enquanto a
macrorregião não foi colocada, a ficha é a soma recalculada a cada leitura;
colocada, a ficha é a linha gravada. É ela que a Regional preenche e que o MOTOR
lê — recalcular por cima faria a tela mostrar um número e a rodada usar outro no
mesmo instante. O preço é que uma recarga do Databricks que mude os membros não
se propaga sozinha para uma macrorregião já colocada.

**As quatro obras nascem vazias, e isso é o desenho.** O `PUT` recusa
cardinalidade incompleta (`obras_da_ficha`) e nunca cria a obra que falta: para
uma CTS do Databricks as quatro sempre vieram na carga, e a macrorregião não vem
de carga nenhuma. Grava-se só o vocabulário — nome e unidade de medida, iguais
nas 337 CTS do banco. Nenhum número, pela mesma razão que tirou a "base literal"
de `obras_da_ficha`: valor plausível que ninguém digitou é pior que valor
ausente.

**Disponível passou a ter dois lados.** Uma macrorregião só é oferecida se
NENHUM membro está em sistema e ela própria não está — o segundo lado não existia
antes de ela ter onde estar colocada. Sem ele, uma macrorregião já montada
continuaria na lista, e a segunda colocação a mudaria de sistema sem ninguém ter
pedido (`dominio.macrorregiao_cts.livres`).

**Membro não se coloca sozinho.** Com a unidade marcada, colocar um coletor que
pertence a uma macrorregião é recusado nomeando a macrorregião. A tela já não o
oferece; a recusa existe para quem tinha a lista antiga aberta ou chama a rota
direto.

**A prontidão não precisou mudar.** Ela conta componente COLOCADO na topologia
(ADR 0006): a macrorregião entra por ser nó, e os membros ficam de fora por não
serem. Cobra-se uma ficha e quatro obras por macrorregião, e não por coletor.

**Desmarcar com macrorregião colocada é RECUSADO**, e essa é a decisão que
fecha o ciclo. Desmarcada, a unidade devolve os coletores membros à lista de
disponíveis — enquanto a macrorregião que os SOMA continua no sistema. Colocado
um membro, o motor cria nó para os dois (o nó nasce de `sistema_topologia` para
todo componente que exista em `cts_operacional`), e as mesmas ligações, a mesma
receita e as mesmas obras entram na conta duas vezes, sem que nada na tela
denuncie. É o espelho da regra que já recusava MARCAR com um sistema de duas CTS,
e a saída é explícita: tirar a macrorregião do sistema, o que devolve os
coletores dela à lista, e só então desmarcar. A linha e o que a Regional
preencheu nela permanecem.

**Nome de macrorregião repetido entre empresas é irrepresentável, e recusado.** A
chave é o par `(sistema_cts, emp_codigo)`, mas o cadastro guarda UM id —
`cts_operacional.cts` é chave primária, e nela `MACRO_A` só cabe uma vez. Duas
empresas da unidade com esse nome são duas macrorregiões: fundi-las somaria
coletores que empresas diferentes operam, e escolher uma faria a outra sumir sem
aviso. Um nome assim não é oferecido para montar o sistema
(`macrorregiao_cts.livres`) e não é aceito para colocar
(`_preparar_macrorregiao`), que o nomeia como dado de origem para arrumar. Não há
um caso desses na base de 03/09/2026; a regra existe porque a alternativa é somar
em silêncio no dia em que houver.
