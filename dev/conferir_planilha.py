"""A planilha e o banco dizem a mesma coisa? Aba por aba, coluna por coluna.

A pergunta que motivou isto: "a planilha esta completa, entao nao deveriamos ter
dado faltante — a nao ser que algo tenha mudado". Este script responde
literalmente isso, e separa as duas causas possiveis, que exigem acoes opostas:

  FALTA NA ORIGEM   a planilha ja nao tem o dado. Carregar de novo nao resolve;
                    e trabalho de cadastro.
  DIVERGIU          a planilha tem X e o banco tem Y. Alguem mexeu depois da
                    carga — pela tela, por script, ou por SQL solto.

Nao usa o carregador (`rodar_simulacao_real.py`) de proposito: se os dois
compartilhassem codigo, um erro de leitura apareceria dos dois lados e se
cancelaria. Aqui a planilha e lida do zero.
"""

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

PACOTE = Path(
    r"C:\Users\LúcioFláviodosSantos\OneDrive - Peers Consulting\Área de Trabalho"
    r"\aegea\Otimizador_CAPEX_v62_pacote_rev11\Otimizador_CAPEX_v62_pacote"
)
BANCO = PACOTE / "banco_dados_regional_v29_completo.xlsx"
PG = "postgresql://otim:otim@localhost:55432/otimizador"

#: Aba -> (tabela, chave primaria para casar as linhas)
ABAS = {
    "unidade-regional": ("unidade_regional", "unidade_id"),
    "regional-superintendencia": ("regional_superintendencia", "superintendencia_id"),
    "superintendencia-cidade": ("superintendencia_cidade", "cidade_id"),
    "cidade-sistema": ("cidade_sistema", "sistema_id"),
    "sistema-topologia": ("sistema_topologia", "componente_sistema_id"),
    "cidade-operacional": ("cidade_operacional", "cidade_id"),
    "subbacia-operacional": ("subbacia_operacional", "sub_bacia"),
    "componentes-subbacias-capex": ("componentes_subbacias_capex", None),
    "ete-capex": ("ete_capex", None),
    "regional-operacional": ("regional_operacional", None),
    "metas-cobertura": ("metas_cobertura", None),
    "fator-esgoto": ("fator_esgoto", None),
    "subbacia-cts": ("subbacia_cts", "cts"),
    "cts-operacional": ("cts_operacional", "cts"),
    "componentes-cts-capex": ("componentes_cts_capex", None),
}

eng = create_engine(PG)
achados: list[str] = []


def col_do_banco(tabela: str) -> set[str]:
    with eng.connect() as c:
        return {
            r[0]
            for r in c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='input' AND table_name=:t"
                ),
                {"t": tabela},
            )
        }


print(f"planilha: {BANCO.name}\n")
print(f"{'aba':<30} {'planilha':>9} {'banco':>7}  {'situacao'}")
print("-" * 78)

for aba, (tabela, chave) in ABAS.items():
    try:
        df = pd.read_excel(BANCO, sheet_name=aba)
    except ValueError:
        print(f"{aba:<30} {'—':>9} {'—':>7}  aba ausente na planilha")
        continue

    with eng.connect() as c:
        n_banco = c.execute(text(f"SELECT count(*) FROM input.{tabela}")).scalar()

    delta = n_banco - len(df)
    marca = "ok" if delta == 0 else f"DIFERENCA {delta:+d} linhas"
    if delta:
        achados.append(f"{tabela}: {len(df)} na planilha, {n_banco} no banco ({delta:+d})")
    print(f"{aba:<30} {len(df):>9} {n_banco:>7}  {marca}")

    # --- colunas 100% vazias JA NA PLANILHA: falta de origem, nao perda na carga
    cols_banco = col_do_banco(tabela)
    vazias = [c for c in df.columns if c in cols_banco and df[c].notna().sum() == 0]
    if vazias:
        achados.append(f"{tabela}: vazias NA ORIGEM -> {', '.join(vazias)}")
        print(f"{'':<30} {'':>9} {'':>7}  vazias na planilha: {', '.join(vazias)}")

    # --- divergencia linha a linha, so onde ha chave simples
    if not chave or chave not in df.columns or chave not in cols_banco:
        continue
    comuns = [c for c in df.columns if c in cols_banco and c != chave]
    if not comuns:
        continue
    with eng.connect() as c:
        banco = pd.read_sql(
            text(f'SELECT {chave}, {", ".join(comuns)} FROM input.{tabela}'), c
        )
    j = df[[chave, *comuns]].merge(banco, on=chave, suffixes=("_pl", "_bd"))
    for col in comuns:
        a, b = j[f"{col}_pl"], j[f"{col}_bd"]
        # NaN == NULL nao e divergencia; comparar como texto evita 1 != 1.0
        dif = j[~(a.isna() & b.isna()) & (a.astype(str) != b.astype(str))]
        # numerico: comparar valor, nao a grafia ("1" vs "1.0")
        if len(dif):
            try:
                na, nb = pd.to_numeric(dif[f"{col}_pl"]), pd.to_numeric(dif[f"{col}_bd"])
                dif = dif[(na - nb).abs() > 1e-9]
            except (ValueError, TypeError):
                pass
        if len(dif):
            ids = ", ".join(str(v) for v in dif[chave].head(4))
            achados.append(
                f"{tabela}.{col}: {len(dif)} linha(s) DIVERGEM da planilha ({ids})"
            )
            print(f"{'':<30} {'':>9} {'':>7}  {col}: {len(dif)} divergem ({ids})")

print("\n" + "=" * 78)
if not achados:
    print("O banco reproduz a planilha.")
else:
    print("ACHADOS\n")
    for a in achados:
        print(f"  - {a}")
sys.exit(1 if achados else 0)
