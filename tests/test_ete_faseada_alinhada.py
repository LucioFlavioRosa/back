"""OS DOIS EXECUTORES TÊM DE RODAR O MESMO PROBLEMA.

`ete_faseada` é o interruptor que faz a ETE existir como MÓDULOS: com ele, a ETE nova
entra como pacote inicial e ganha módulos de expansão se a vazão pedir; a que já existe é
expandida em módulos conforme a demanda. **Sem ele, a ETE não vira obra construível, nunca
fica pronta, e o motor recusa a receita de toda sub-bacia do sistema.** Medido na uA1 em
29/09/2026, no mesmo cadastro e no mesmo plano:

    ete_faseada=True     142 sub-bacias faturando   R$ 744.050.138,78 de receita
    ete_faseada=False      0 sub-bacias faturando   R$          0,00

Não é outra modelagem. É rodada quebrada.

## A contradição que este arquivo existe para impedir

Até 29/09/2026 o parâmetro NÃO viajava no pedido, e a regra era "quem afirma `True` é o
executor". Três lugares, cada um certo isoladamente:

    app/dominio/parametros.py   não mandava a chave, de propósito
    dev/worker.py               afirmava `ete_faseada=True`
    job_databricks.py           não afirmava nada ("chave ausente não vira default do
                                job"), e o default do `ler_banco` é False

Juntos: o MESMO pedido rodava faseado no executor local e não-faseado em produção.

A correção põe a regra no PEDIDO, que é a única fonte que os dois executores compartilham.
Os testes abaixo prendem os três pontos — e o do worker lê o fonte porque o que se
protege é justamente uma linha que já foi apagada uma vez de um dos lados.
"""
from pathlib import Path

import pytest

from app.dominio.parametros import CHAVES_ACEITAS, montar_params
from app.infra.repositorios.controle import digest

WORKER = Path("dev/worker.py")


def _pedido(**extra):
    corpo = {"unidade_id": "uA1", "orcamento": {2026: 60e6, 2027: 60e6}, **extra}
    return montar_params(corpo, unidade_id="uA1", usuario="lucio.rosa")


def test_o_pedido_afirma_o_modo():
    """Primeiro ponto: a regra sai do backend, em vez de cada executor decidir."""
    assert _pedido()["ETE_FASEADA"] is True


def test_a_chave_e_aceita_pelo_job():
    """Sem isto o job recusaria o pedido — chave desconhecida é ERRO lá, não silêncio."""
    assert "ETE_FASEADA" in CHAVES_ACEITAS


def test_o_worker_le_o_pedido_e_nao_um_valor_fixo():
    """Segundo ponto, e é o que estava escondido: o executor local afirmava por conta.

    Enquanto ele fixava `ete_faseada=True`, a divergência com o job era invisível — o
    desenvolvimento rodava certo e produção rodava outro problema. Lendo do pedido, os
    dois passam a ler a MESMA fonte.
    """
    fonte = WORKER.read_text(encoding="utf-8")
    assert 'ete_faseada=bool(p.get("ETE_FASEADA", True))' in fonte
    assert "ete_faseada=True," not in fonte, (
        "o worker voltou a fixar o modo; a divergência com o job volta com ele"
    )


def test_o_worker_mantem_default_True_para_pedido_ANTIGO():
    """Os pedidos gravados antes de 29/09/2026 não têm a chave, e todos rodaram faseado.

    Cair no default False do motor num retry os faria rodar outro problema — e o sintoma
    seria receita zero, não um erro.
    """
    fonte = WORKER.read_text(encoding="utf-8")
    assert '.get("ETE_FASEADA", True)' in fonte, "o default para pedido antigo sumiu"


def test_o_modo_entra_no_digest_da_deduplicacao():
    """Terceiro efeito, de brinde: faseado e não-faseado deixam de ser a mesma simulação.

    Enquanto o modo não viajava, ele não entrava no digest — duas rodadas com modos
    diferentes eram "o mesmo pedido" para a dedupe. Agora são pedidos distintos.
    """
    normal = _pedido()
    sem_faseamento = {**normal, "ETE_FASEADA": False}
    assert digest(normal) != digest(sem_faseamento)


@pytest.mark.parametrize("valor", [True, False])
def test_a_tela_nao_desliga_o_faseamento(valor):
    """O corpo não manda o modo, e mandar não muda nada.

    A tela não oferece a escolha, e não deveria: uma rodada com receita zero não é
    alternativa de análise. Se o produto quiser oferecer, o caminho é aceitar o campo
    aqui — e ele já entra no digest, porque viaja no pedido.
    """
    assert _pedido(ete_faseada=valor)["ETE_FASEADA"] is True


def test_A_REGRA_ESTA_ESCRITA_ONDE_ELA_VIVE():
    """O porquê fica junto do código, e não só no histórico do git.

    A contradição durou porque cada um dos três lugares documentava a própria metade. O
    teste cobra que o lugar que hoje decide explique o que acontece sem o modo — é o que
    faz a próxima pessoa não "simplificar" a linha.
    """
    fonte = Path("app/dominio/parametros.py").read_text(encoding="utf-8")
    trecho = fonte[fonte.index('params["ETE_FASEADA"]') - 2500:fonte.index('params["ETE_FASEADA"]')]
    assert "job_databricks" in trecho, "falta dizer qual executor divergia"
    assert "744" in trecho or "ZERO" in trecho, "falta o número que mostra o estrago"


def test_A_VARIACAO_DE_RODADA_ANTIGA_TAMBEM_AFIRMA_O_MODO():
    """A sensibilidade CLONA os params da origem, e a origem pode ser antiga.

    Era o buraco que sobrava depois de o `POST /runs` passar a afirmar: `params_da_variacao`
    faz `{**base, ...}`, e as 127 rodadas gravadas antes de 29/09/2026 não têm a chave. O
    ponto da curva nasceria sem ela e, no job de produção, rodaria NÃO-FASEADO — receita
    zero, comparado contra uma origem que faturou. A curva mostraria uma queda que é
    artefato do modo, não do orçamento.
    """
    from app.dominio.variacao import params_da_variacao

    base_antiga = {           # como um `run_request` de antes da mudança
        "UNIDADE": "uA1",
        "BASE_RECEITA": "arrecadada",
        "USAR_CTS": True,
        "FOCO_COBERTURA": 1.0,
        "ORCAMENTO": {2026: 60e6, 2027: 60e6},
    }
    assert "ETE_FASEADA" not in base_antiga
    variacao = params_da_variacao(
        base_antiga, unidade_id="uA1", usuario="lucio.rosa", fator=1.1, modo="completo"
    )
    assert variacao["ETE_FASEADA"] is True


def test_OS_DOIS_CAMINHOS_DE_CRIACAO_LEEM_A_MESMA_CONSTANTE():
    """Uma definição, e não um literal em cada lugar.

    São dois caminhos que criam rodada — o `POST /runs` e a variação —, e a regra já
    divergiu uma vez por estar escrita em dois lugares. O teste cobra que nenhum dos dois
    tenha o valor cravado.
    """
    from app.dominio.parametros import ETE_FASEADA_SEMPRE

    assert ETE_FASEADA_SEMPRE is True
    for arquivo in ("app/dominio/parametros.py", "app/dominio/variacao.py"):
        fonte = Path(arquivo).read_text(encoding="utf-8")
        assert "ETE_FASEADA_SEMPRE" in fonte, f"{arquivo} não usa a constante"
        assert '"ETE_FASEADA": True' not in fonte, f"{arquivo} cravou o valor"
        assert 'params["ETE_FASEADA"] = True' not in fonte, f"{arquivo} cravou o valor"
