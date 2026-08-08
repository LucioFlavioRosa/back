"""Apaga o banco e reconstroi tudo a partir da planilha: input + as 5 rodadas.

Existe porque `rodar_simulacao_real.py` recarrega a planilha inteira a cada
execucao (ele trunca `input` no comeco). Rodar as 5 unidades chamando-o cinco
vezes carregaria 4850 sub-bacias e 24250 componentes cinco vezes, sem motivo:
o `input` e o MESMO para todas — o que muda por unidade e so o recorte que o
motor le.

Tambem limpa o que ele NAO limpa: as tabelas de resultado (`public.otim_*`), a
fila (`controle.run_*`) e a trilha de override. Sem isso sobram rodadas apontando
para um `input` que nao existe mais, e o historico do front mostra numero de um
cadastro que foi substituido.

  python dev/recarregar_tudo.py            # as 5 unidades
  python dev/recarregar_tudo.py uA1 uB2    # so essas
"""

import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).parent))
import rodar_simulacao_real as R  # noqa: E402

UNIDADES = sys.argv[1:] or ["uA1", "uA2", "uA3", "uB1", "uB2"]
PG = R.PG

# `otim_auditoria` fica de fora do TRUNCATE em cascata por seguranca: se um dia
# ela guardar historico que nao se reconstroi, apagar aqui seria perda real.
RESULTADO = """
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'otim_%'
  LOOP EXECUTE format('TRUNCATE public.%I CASCADE', t); END LOOP;
END $$;
TRUNCATE controle.run_diagnostico, controle.run_status, controle.run_request CASCADE;
TRUNCATE input.override;
"""


def main() -> None:
    eng = create_engine(PG)
    print("apagando resultados, fila e trilha de override...")
    with eng.begin() as con:
        con.execute(text(RESULTADO))

    print("\ncarregando a planilha (uma vez)...")
    t0 = time.time()
    R.carregar_input()
    print(f"  {time.time() - t0:.0f}s")

    # Cada unidade roda em processo PROPRIO: o motor guarda estado de modulo
    # entre execucoes (engine, caches), e reaproveitar o processo ja misturou
    # cenario de uma unidade com o de outra. Processo novo nao tem esse risco.
    #
    # `--sem-carga` nao existe no script original — por isso o subprocesso roda
    # `rodar_e_publicar` direto, e nao o `__main__` dele (que recarregaria).
    for i, u in enumerate(UNIDADES, 1):
        print(f"\n{'=' * 60}\n[{i}/{len(UNIDADES)}] {u}\n{'=' * 60}")
        t0 = time.time()
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.argv=['x', %r]; "
                "sys.path.insert(0, 'dev'); "
                "import rodar_simulacao_real as R; "
                "print(R.rodar_e_publicar())" % u,
            ],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True,
            text=True,
        )
        print(r.stdout[-1500:] if r.stdout else "")
        if r.returncode:
            print(f"  FALHOU ({r.returncode})\n{r.stderr[-1500:]}")
        print(f"  {time.time() - t0:.0f}s")

    with eng.begin() as con:
        n = con.execute(text("SELECT count(*) FROM public.otim_meta")).scalar()
        subs = con.execute(text("SELECT count(*) FROM input.subbacia_operacional")).scalar()
        cts = con.execute(text("SELECT count(*) FROM input.cts_operacional")).scalar()
    print(f"\n{'=' * 60}\nrodadas publicadas: {n}   sub-bacias: {subs}   CTS: {cts}")


if __name__ == "__main__":
    main()
