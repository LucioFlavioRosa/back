"""A INTERFACE PÚBLICA de `app/dominio/` — declarada, e não deduzida.

`__all__` diz o que cada módulo do domínio oferece; o resto é implementação e
pode mudar sem aviso. A lista só vale enquanto acompanha o módulo, e é
exatamente aí que ela falha na prática: alguém acrescenta uma função pública, não
mexe no `__all__`, e a partir dali a declaração passa a mentir — com a agravante
de que nada quebra. O import direto continua funcionando, o `import *` não traz
a função nova, e ninguém percebe até alguém confiar na lista.

Estes testes são a manutenção automática dessa lista. Eles varrem o pacote, e não
uma enumeração escrita à mão: um módulo novo em `app/dominio/` entra na varredura
sem ninguém lembrar de incluí-lo.
"""

import ast
import importlib
import io
import pathlib

import pytest

DOMINIO = pathlib.Path(__file__).resolve().parents[1] / "app" / "dominio"

#: Os módulos do domínio, descobertos pelo diretório. `__init__` fica de fora:
#: ele é o pacote, não um módulo de regra.
MODULOS = sorted(p.stem for p in DOMINIO.glob("*.py") if p.stem != "__init__")


def nomes_publicos_no_codigo(nome: str) -> set[str]:
    """Tudo o que o ARQUIVO define no topo sem `_` na frente.

    Por AST e não por `dir()`: `dir()` traz também o que o módulo importou de
    fora (`Any`, `Literal`, `math`), e esses nunca são interface — são
    ferramenta. A pergunta é "o que este módulo DEFINE e oferece", e só o código
    responde.
    """
    arvore = ast.parse(io.open(DOMINIO / f"{nome}.py", encoding="utf-8").read())
    achados: set[str] = set()
    for no in arvore.body:
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not no.name.startswith("_"):
                achados.add(no.name)
        elif isinstance(no, ast.Assign):
            for alvo in no.targets:
                if isinstance(alvo, ast.Name) and not alvo.id.startswith("_"):
                    achados.add(alvo.id)
        elif isinstance(no, ast.AnnAssign) and isinstance(no.target, ast.Name):
            if not no.target.id.startswith("_"):
                achados.add(no.target.id)
    return achados - {"__all__"}


def test_ha_modulos_para_conferir():
    # Guarda contra o teste passar por não encontrar nada — se a varredura
    # quebrar, os testes abaixo passariam vazios e diriam que está tudo certo.
    assert len(MODULOS) >= 10


@pytest.mark.parametrize("nome", MODULOS)
def test_todo_modulo_declara_sua_interface(nome):
    mod = importlib.import_module(f"app.dominio.{nome}")
    assert hasattr(mod, "__all__"), (
        f"`app/dominio/{nome}.py` não declara `__all__`. Sem ela, quem lê o "
        "módulo não distingue contrato de andaime."
    )
    assert mod.__all__, "lista vazia diz que o módulo não oferece nada"


@pytest.mark.parametrize("nome", MODULOS)
def test_o_que_a_lista_promete_existe(nome):
    mod = importlib.import_module(f"app.dominio.{nome}")
    faltando = [n for n in mod.__all__ if not hasattr(mod, n)]
    assert not faltando, f"`__all__` de {nome} cita o que não existe: {faltando}"


@pytest.mark.parametrize("nome", MODULOS)
def test_a_lista_NAO_ESQUECE_NADA_publico(nome):
    """O teste que importa, e o defeito que ele prende.

    Uma função pública fora do `__all__` não quebra nada: o import direto
    continua funcionando. Ela só some do `import *` e da leitura de quem confia
    na lista — e a divergência cresce em silêncio a cada função nova.

    Se este teste falhar, há duas saídas certas e uma errada: acrescentar o nome
    ao `__all__` (é interface) ou prefixá-lo com `_` (é implementação). A errada
    é relaxar o teste.
    """
    mod = importlib.import_module(f"app.dominio.{nome}")
    esquecidos = nomes_publicos_no_codigo(nome) - set(mod.__all__)
    assert not esquecidos, (
        f"{nome} define sem declarar: {sorted(esquecidos)}. "
        "Ou entra no `__all__`, ou ganha `_` na frente."
    )


@pytest.mark.parametrize("nome", MODULOS)
def test_a_lista_NAO_PROMETE_o_que_e_de_fora(nome):
    # `__all__` re-exportando o que o módulo importou faria dele um atalho para
    # outro — e mudar o outro passaria a quebrar quem importou daqui.
    mod = importlib.import_module(f"app.dominio.{nome}")
    alheios = set(mod.__all__) - nomes_publicos_no_codigo(nome)
    assert not alheios, f"{nome} exporta o que não define: {sorted(alheios)}"
