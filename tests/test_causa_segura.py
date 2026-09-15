"""A causa da falha vai para a tela sem credencial, sem caminho de máquina, no tamanho certo."""
from app.dominio.causa import CAUSA_MAX, causa_segura


def test_credencial_em_url_some_e_o_host_fica():
    assert causa_segura("OperationalError: postgresql://otim:s3gr3do@db:5432/otimizador recusou") == (
        "OperationalError: postgresql://***@db:5432/otimizador recusou"
    )


def test_segredo_nomeado_perde_o_valor_e_mantem_o_nome():
    s = causa_segura("Endpoint=sb://x;SharedAccessKeyName=Root;SharedAccessKey=ABC123; password=abc")
    assert "ABC123" not in s and "abc" not in s.split("password")[1]
    assert "SharedAccessKey=***" in s and "password=***" in s
    # o NOME da chave nao e segredo e continua legivel
    assert "SharedAccessKeyName=Root" in s


def test_caminho_de_arquivo_vira_so_o_nome():
    assert causa_segura(r"FileNotFoundError: C:\Users\fulano\projetos\pacote\otimizador_capex_v62.py") == (
        "FileNotFoundError: otimizador_capex_v62.py"
    )
    assert causa_segura("KeyError em /home/app/otimizador/persistencia.py linha 12") == (
        "KeyError em persistencia.py linha 12"
    )


def test_tamanho_e_espacos():
    assert len(causa_segura("x" * 2000)) == CAUSA_MAX
    assert causa_segura("a\n\n  b\t c") == "a b c"


def test_none_continua_none_e_a_mensagem_comum_nao_muda():
    assert causa_segura(None) is None
    comum = "O processo desta rodada morreu (falha nativa no solver ou na materialização)."
    assert causa_segura(comum) == comum
    solver = "RuntimeError: O solver falhou ao reparar o teto anual: a cidade 'Mesquita' ficou sem coluna selecionada."
    assert causa_segura(solver) == solver
