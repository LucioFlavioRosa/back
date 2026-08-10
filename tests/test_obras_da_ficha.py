"""O que vai para o banco quando a ficha é gravada.

A gravação é `DELETE` + `INSERT` do bloco inteiro. Isso torna duas coisas
perigosas, e a segunda escondia a primeira:

  componente OMITIDO no corpo  -> seria APAGADO
  campo omitido dentro dele    -> viraria NULL

A base literal (`_BASE_SUBBACIA`) mascarava as duas: completava com valores de
template, então em vez de sumir, o componente reaparecia com números plausíveis
que ninguém digitou. Corrupção silenciosa no lugar de perda silenciosa — e a
plausibilidade é o que a torna pior, porque ninguém desconfia de um preço redondo.

A regra agora, na ordem: **o corpo manda; o que ele não trouxer vem da LINHA
GRAVADA; o literal só alcança componente que nunca existiu.** E componente que
existe e não veio é recusa — a tela não oferece remover obra, então omissão nunca
é intenção.
"""

import pytest

from app.infra.repositorios.cadastro_escrita import ValorInvalido, _obras_da_ficha

BASE = [
    {"nome": "Ligacao de esgoto", "un": "ligacao", "qtd": "244", "preco": "2.497,70"},
    {"nome": "Rede coletora", "un": "m", "qtd": "2.472,6", "preco": "449,99"},
    {"nome": "Coletor tronco", "un": "m", "qtd": "0", "preco": "1.200,00"},
]
#: O que o banco tem hoje — diferente da base, como todo dado real é.
ATUAL = {
    "0": {"qtd": "300", "preco": "3.000,00"},
    "1": {"qtd": "1.111", "preco": "500,00"},
    "2": {"qtd": "9", "preco": "1.500,00"},
}


def test_campo_omitido_vem_do_banco_e_nao_do_literal():
    """O caso que corrompia: o corpo traz só `qtd`, e `preco` tinha de sobreviver."""
    obras = _obras_da_ficha({"0": {"qtd": "999"}, "1": {}, "2": {}}, BASE, ATUAL)
    assert obras[0]["qtd"] == "999"  # o que o usuário digitou
    assert obras[0]["preco"] == "3.000,00"  # o do BANCO, não os 2.497,70 da base
    assert obras[1]["preco"] == "500,00"
    assert obras[2]["qtd"] == "9"


def test_componente_omitido_e_recusa():
    """Sem isto ele seria apagado — ou, pior, regravado com valor de template."""
    with pytest.raises(ValorInvalido) as e:
        _obras_da_ficha({"0": {"qtd": "1"}, "1": {}}, BASE, ATUAL)
    # A mensagem diz QUAL faltou: "3 de 5" sozinho não ajuda ninguém a corrigir.
    assert "Coletor tronco" in str(e.value)


def test_corpo_completo_passa_e_o_corpo_manda():
    obras = _obras_da_ficha(
        {"0": {"qtd": "1"}, "1": {"qtd": "2"}, "2": {"qtd": "3"}}, BASE, ATUAL
    )
    assert [o["qtd"] for o in obras] == ["1", "2", "3"]


def test_ficha_sem_obras_no_banco_usa_o_literal():
    """O único caso em que o literal ainda vale: componente que nunca existiu.

    Não acontece com o dado da planilha (toda ficha tem 5 e 4), mas é o que dá
    forma à lista quando ela nasce vazia.
    """
    obras = _obras_da_ficha({"0": {"qtd": "7"}}, BASE, {})
    assert obras[0]["qtd"] == "7"
    assert obras[1]["preco"] == "449,99"
    assert len(obras) == 3


def test_lista_antiga_continua_passando():
    """Forma antiga (lista pronta), que os smokes locais ainda usam."""
    assert _obras_da_ficha([{"qtd": "1"}], BASE, ATUAL) == [{"qtd": "1"}]
