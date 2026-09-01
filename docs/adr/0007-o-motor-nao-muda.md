# Restrição nova é cadastro ou parâmetro de rodada, nunca código do motor

O motor de otimização (`otimizador_capex_v62.py`, fora deste repositório) é
tratado como dado: nada aqui altera a formulação dele. Uma exigência nova — obra
obrigatória num ano, obra proibida até um ano, orçamento por ano — entra como
coluna de cadastro que ele já lê ou como parâmetro do pedido, e é por isso que
`obra_obrigatoria_ano` e `obra_proibida_ate` existem nas três abas de obra.

## Consequences

Quando uma restrição não tem onde ser expressa, o caminho é abrir a coluna na
tela e no backend, não mexer no solver. É uma constraint de projeto e não se
deduz do código: quem olhar só para este repositório não vê o motor, e pode
supor que ele é editável.
