"""Escopo e posse são DUAS perguntas, e `admin` só responde uma delas.

  ESCOPO   quais unidades a pessoa acessa. Vale para todo mundo, inclusive admin.
  POSSE    de quem é a rodada. `admin` relaxa isto, e só isto.

O código já misturou as duas: `GET /runs` fazia `if quem.admin or quem.tudo:
return linhas`, e por causa daquele `admin` um administrador de UMA regional
listava o banco inteiro. Papel e escopo viravam a mesma coisa — e a tabela de
concessão deixava de significar algo justamente para quem mais precisa dela.

Testado aqui na `Identidade`, sem banco: é onde a regra mora, e um teste que
precisa de Postgres para afirmar uma condição booleana não é o guarda que se
quer para uma regra de autorização.
"""

from app.api.deps import Identidade

ANALISTA = Identidade(
    login="ana@aegea",
    papeis=frozenset({"analista"}),
    unidades=frozenset({"uA1", "uA2", "uA3"}),
)
ADMIN_REGIONAL = Identidade(
    login="chefe@aegea",
    papeis=frozenset({"admin"}),
    unidades=frozenset({"uA1", "uA2", "uA3"}),
)
ADMIN_TOTAL = Identidade(login="dev@local", papeis=frozenset({"admin"}), tudo=True)
SEM_NADA = Identidade(login="ninguem@aegea")


def test_escopo_vale_para_todos_inclusive_admin():
    """O `admin` de uma regional NÃO alcança outra."""
    assert ADMIN_REGIONAL.acessa_unidade("uA1")
    assert not ADMIN_REGIONAL.acessa_unidade("uB2")
    assert ANALISTA.acessa_unidade("uA1")
    assert not ANALISTA.acessa_unidade("uB2")
    assert not SEM_NADA.acessa_unidade("uA1")


def test_escopo_total_alcanca_tudo():
    assert ADMIN_TOTAL.acessa_unidade("uA1")
    assert ADMIN_TOTAL.acessa_unidade("uB2")


def test_posse_e_o_que_admin_relaxa():
    """Analista vê só as próprias; admin vê as dos colegas."""
    assert ANALISTA.ve_rodada_de("ana@aegea")
    assert not ANALISTA.ve_rodada_de("carlos@aegea")
    assert ADMIN_REGIONAL.ve_rodada_de("ana@aegea")
    assert ADMIN_REGIONAL.ve_rodada_de("carlos@aegea")


def test_posse_ignora_caixa_do_login():
    """O login vem do token e a caixa não é garantida; o dono, do banco."""
    assert ANALISTA.ve_rodada_de("ANA@AEGEA")


def test_rodada_sem_dono_e_so_do_admin():
    """A primeira versão deixava passar para todo mundo, com o argumento de não
    esconder dado anterior ao recorte. `otim_meta.usuario` aceita NULL, então
    bastava um script publicar sem autor para a rodada virar legível por qualquer
    um que soubesse o `run_id` — brecha que se abre por descuido de carga é pior
    que a que exige ataque."""
    assert not ANALISTA.ve_rodada_de(None)
    assert ADMIN_REGIONAL.ve_rodada_de(None)


def test_papel_desconhecido_nao_da_poder():
    """A tabela pode crescer antes do código: papel que o serviço não entende é
    guardado e IGNORADO, nunca interpretado como privilégio."""
    outro = Identidade(login="x@aegea", papeis=frozenset({"auditor", "revisor"}))
    assert not outro.admin
    assert not outro.ve_rodada_de("ana@aegea")
