"""Carrega a EXPORTAÇÃO REAL do portfólio (os três CSV do Databricks) numa base nova.

    python dev/carregar_portfolio.py <pasta-dos-csv> [banco]      # padrao: otimizador_real

A base de trabalho (`otimizador`) NÃO é tocada: esta carga cria outra, copia o
esquema dela (todas as migrações, como estão), e preenche só `input`. Trocar o
serviço de uma para a outra é `POSTGRES_URL`; voltar é a mesma linha.

O QUE OS CSV TRAZEM, E O QUE NÃO TRAZEM. Trazem a camada COMERCIAL — hierarquia,
cidades, sistemas, sub-bacias e coletores, com as medidas do Databricks. NÃO
trazem o que a Regional preenche (preço, prazos, vazão, população, as obras),
nem ETE, nem o desenho da topologia (para onde cada componente escoa). É a
fronteira certa: a carga entrega o que se mede, e o cadastro recebe o que se
decide. Por isso a base nascida daqui está INCOMPLETA por definição — a prontidão
vai cobrar tudo, e é esse o estado real de uma unidade recém-carregada.

AS OBRAS NASCEM COM VOCABULÁRIO. O `PUT` da ficha recusa cardinalidade
incompleta e nunca cria a obra que falta (`obras_da_ficha`), então cada
sub-bacia nasce com as 5 e cada coletor com as 4 — nome e unidade de medida, e
nenhum número. É a mesma regra da macrorregião (`_preparar_macrorregiao`).

UM SES EM VÁRIAS CIDADES É UM SISTEMA SÓ, em várias cidades. A origem tem 12
assim (Sarapuí em 5, Pavuna em 3) e um deles atravessa empresa (Saracuruna: Duque
de Caxias é a 57, Magé é a 56). Desde a migração 022 o esquema comporta isso:
`input.sistema` é a entidade, `cidade_sistema` tem uma linha por cidade que o
sistema atende, e cada sub-bacia sabe a própria cidade.

OS IDS SAEM DOS NOMES, por `slug`: a origem não manda id de sub-bacia, de
sistema nem de cidade, e um id gerado por sequência mudaria a cada carga. O
coletor é a exceção — `CTS` já é um código (`100_SARAPUINL_NILOPOLIS`) e entra
como está.
"""

import asyncio
import csv
import io
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.dominio.campos import OBRAS_DA_CTS, OBRAS_DA_SUBBACIA  # noqa: E402

CONTAINER = "otimizador-backend-db-1"
PORTA = 55432
USUARIO = SENHA = "otim"
BASE_MODELO = "otimizador"

PASTA = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Downloads"
BANCO = sys.argv[2] if len(sys.argv) > 2 else "otimizador_real"

#: O QUE ESTE SCRIPT NUNCA APAGA. Ele faz `DROP DATABASE` no nome que receber, e
#: um segundo argumento errado — `otimizador`, no cansaço — levaria a base de
#: trabalho junto, com tudo que a Regional preencheu. A lista e curta e
#: explicita; o nome tambem tem de dizer que e uma carga (`_real`, `_teste`...).
INTOCAVEIS = {"", "otimizador", "otim_revisao", "postgres", "template0", "template1"}
if BANCO in INTOCAVEIS or "_" not in BANCO:
    raise SystemExit(
        f"recusado: '{BANCO}' nao e nome de base de carga. Use um nome com sufixo, "
        "como 'otimizador_real' — este script APAGA a base que receber."
    )
PREFIXO = "PORTFOLIO_INVEST_CAPEX_SUBBACIAS_v5_"

#: CSV -> coluna da ficha de coleta. As oito primeiras têm par `_COM_CTS` na
#: sub-bacia, e o par vai para a coluna `*_com_cts` correspondente.
MEDIDAS = {
    "QTD_LIGACOES_TOTAL": "universo_ligacoes",
    "QTD_LIGACOES_AGUA": "ligacoes_atuais",
    "QTD_LIGACOES_RES": "universo_ligacoes_residencial",
    "QTD_LIGACOES_RES_AGUA": "ligacoes_atuais_residencial",
    "QTD_ECO_TOTAL": "universo_economias",
    "QTD_ECO_AGUA": "economias_atuais",
    "QTD_ECO_RES": "universo_economias_residencial",
    "QTD_ECO_RES_AGUA": "economias_atuais_residencial",
    "MED_12M_FAT_DIR_AGUA_LIQ": "receita_faturada_media_mensal",
    "MED_12M_ARREC_DIR_AGUA": "receita_arrecadada_media_mensal",
}
INTEIRAS = {c for c in MEDIDAS.values() if not c.startswith("receita")}


def slug(texto: str) -> str:
    """`(AP) - 08 (FAGUNDES)` -> `ap_08_fagundes`. Determinístico, sem acento, sem símbolo."""
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_").lower()
    return t or "x"


def ler(nome: str) -> list[dict[str, str]]:
    p = PASTA / (PREFIXO + nome)
    linhas = [l.strip() for l in io.open(p, encoding="utf-8-sig") if l.strip()]
    # cada linha vem envolvida em aspas duplas
    linhas = [l[1:-1] if l.startswith('"') and l.endswith('"') else l for l in linhas]
    return list(csv.DictReader(linhas))


def num(v: str, inteira: bool):
    v = (v or "").strip()
    if not v or v.lower() == "null":
        return None
    return int(round(float(v))) if inteira else float(v)


def medidas(x: dict[str, str], sufixo: str = "") -> dict[str, object]:
    """As medidas da linha. Com `sufixo`, as versões `_COM_CTS` — só as OITO de
    quantidade: a origem manda receita e adimplência `_COM_CTS` também, mas a
    sub-bacia só tem coluna para as quantidades (é o que o motor consome)."""
    return {
        col + ("_com_cts" if sufixo else ""): num(x.get(csv_col + sufixo, ""), col in INTEIRAS)
        for csv_col, col in MEDIDAS.items()
        if not sufixo or (col in INTEIRAS and csv_col + sufixo in x)
    }


def sh(cmd: str) -> str:
    r = subprocess.run(["docker", "exec", CONTAINER, "sh", "-c", cmd],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise SystemExit(f"falhou: {cmd}\n{r.stderr}")
    return r.stdout


def criar_base_com_o_esquema() -> None:
    """A base nova nasce com o ESQUEMA da de trabalho — todas as migrações, como estão."""
    existe = sh(f"psql -U {USUARIO} -d postgres -Atc \"SELECT 1 FROM pg_database WHERE datname='{BANCO}'\"").strip()
    if existe:
        print(f"  {BANCO} já existe: apagando para recarregar")
        sh(f"psql -U {USUARIO} -d postgres -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{BANCO}'\" >/dev/null")
        sh(f"psql -U {USUARIO} -d postgres -c 'DROP DATABASE \"{BANCO}\"' >/dev/null")
    sh(f"psql -U {USUARIO} -d postgres -c 'CREATE DATABASE \"{BANCO}\" OWNER {USUARIO}' >/dev/null")
    sh(f"pg_dump -U {USUARIO} --schema-only --no-owner --no-privileges {BASE_MODELO} | psql -U {USUARIO} -d {BANCO} -q")
    # OS ACESSOS VÊM JUNTO. Sem `controle.usuario_acesso` ninguém enxerga regional
    # nenhuma — `GET /regionais` recorta pelo que o usuário acessa, e uma base
    # onde o admin não existe responde uma lista vazia sem erro nenhum.
    sh(f"pg_dump -U {USUARIO} --data-only --no-owner --no-privileges -t controle.usuario_acesso {BASE_MODELO} | psql -U {USUARIO} -d {BANCO} -q")
    print(f"  {BANCO} criada com o esquema e os acessos de {BASE_MODELO}")


async def carregar() -> None:
    hier = ler("portfolio_invest_hierarquia_unidade.csv")
    subs = ler("portfolio_invest_subbacias.csv")
    ctss = ler("portifolio_invest_cts.csv")
    print(f"lidos: {len(hier)} linhas de hierarquia, {len(subs)} sub-bacias, {len(ctss)} coletores")

    # SÓ AS UNIDADES QUE TÊM DADO. A hierarquia lista o grupo inteiro (42 unidades),
    # mas as medidas só vêm de uma; carregar as outras vazias criaria 41 unidades
    # em que toda tela abre em branco.
    empresas_com_dado = {x["EMP_CODIGO"] for x in subs} | {x["EMP_CODIGO"] for x in ctss}
    hier = [h for h in hier if h["EMP_CODIGO"] in empresas_com_dado]

    con = await asyncpg.connect(f"postgresql://{USUARIO}:{SENHA}@localhost:{PORTA}/{BANCO}")
    try:
        async with con.transaction():
            # ---- hierarquia -------------------------------------------------
            regionais = {h["REGIONAL"]: "r" + slug(h["REGIONAL"]).replace("regional_", "") for h in hier}
            await con.executemany("INSERT INTO input.regional (regional_id, regional_name) VALUES ($1,$2)",
                                  sorted({(i, n) for n, i in regionais.items()}))
            diretorias = {(h["REGIONAL"], h["DIRETORIA"]): slug(h["DIRETORIA"]) for h in hier}
            await con.executemany("INSERT INTO input.diretoria (diretoria_id, diretoria_name, regional_id) VALUES ($1,$2,$3)",
                                  sorted({(d, n, regionais[r]) for (r, n), d in diretorias.items()}))
            unidades = {}
            for h in hier:
                uid = slug(h["UNIDADE"])
                unidades[h["UNIDADE"]] = uid
                await con.execute(
                    """INSERT INTO input.unidade_regional
                           (unidade_id, unidade_name, regional_id, regional_name, diretoria_id, diretoria_name)
                       VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (unidade_id) DO NOTHING""",
                    uid, h["UNIDADE"], regionais[h["REGIONAL"]], h["REGIONAL"],
                    diretorias[(h["REGIONAL"], h["DIRETORIA"])], h["DIRETORIA"])
            await con.executemany(
                "INSERT INTO input.empresa (emp_codigo, empresa, unidade_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                [(h["EMP_CODIGO"], h["EMPRESA"], unidades[h["UNIDADE"]]) for h in hier])

            # ---- cidades e sistemas ---------------------------------------
            cidades = {}
            for x in subs + ctss:
                cidades[x["CIDADE"]] = (slug(x["CIDADE"]), x["EMP_CODIGO"])
            await con.executemany("INSERT INTO input.cidade (cidade_id, cidade_name) VALUES ($1,$2)",
                                  sorted({(i, n) for n, (i, _e) in cidades.items()}))
            await con.executemany("INSERT INTO input.cidade_empresa (cidade_id, emp_codigo) VALUES ($1,$2)",
                                  sorted({(i, e) for _n, (i, e) in cidades.items()}))

            # UM SES E UM SISTEMA, em quantas cidades a origem disser — ver o cabeçalho.
            sistemas = {x["SES"]: (slug(x["SES"]), x["SES"]) for x in subs}
            await con.executemany(
                "INSERT INTO input.sistema (sistema_id, sistema_name) VALUES ($1,$2)",
                sorted(set(sistemas.values())))
            await con.executemany(
                "INSERT INTO input.cidade_sistema (sistema_id, sistema_name, cidade_id) VALUES ($1,$2,$3)",
                sorted({(sistemas[x["SES"]][0], x["SES"], cidades[x["CIDADE"]][0]) for x in subs}))

            # ---- sub-bacias -----------------------------------------------
            colunas = None
            linhas_sb, topo_sb, obras_sb = [], [], []
            for x in subs:
                sb = slug(x["SUB_BACIA"])
                m = {**medidas(x), **medidas(x, "_COM_CTS")}
                m["ligacoes_novas_obras"] = (m["universo_ligacoes"] or 0) - (m["ligacoes_atuais"] or 0)
                m["economias_novas_obras"] = (m["universo_economias"] or 0) - (m["economias_atuais"] or 0)
                m["cidade_id"] = cidades[x["CIDADE"]][0]
                if colunas is None:
                    colunas = list(m)
                linhas_sb.append((sb, *[m[c] for c in colunas]))
                topo_sb.append((sb, x["SUB_BACIA"], sistemas[x["SES"]][0]))
                obras_sb += [(sb, nome, un) for nome, un in OBRAS_DA_SUBBACIA]
            marc = ", ".join(f"${i + 2}" for i in range(len(colunas)))
            await con.executemany(
                f"INSERT INTO input.subbacia_operacional (sub_bacia, {', '.join(colunas)}) VALUES ($1, {marc})",
                linhas_sb)
            await con.executemany(
                """INSERT INTO input.sistema_topologia
                       (componente_sistema_id, componente_sistema_nome, sistema_id, componente_sistema_id_jusante)
                   VALUES ($1,$2,$3,NULL)""", topo_sb)
            await con.executemany(
                "INSERT INTO input.componentes_subbacias_capex (sub_bacia, componente, unidade) VALUES ($1,$2,$3)",
                obras_sb)

            # ---- coletores (CTS): FORA de sistema, como manda o conceito -----
            colunas_c = None
            linhas_c, obras_c = [], []
            for x in ctss:
                m = medidas(x)
                m["ligacoes_novas_obras"] = (m["universo_ligacoes"] or 0) - (m["ligacoes_atuais"] or 0)
                m["economias_novas_obras"] = (m["universo_economias"] or 0) - (m["economias_atuais"] or 0)
                m["cidade_id"] = cidades[x["CIDADE"]][0]
                m["sistema_cts"] = x["SISTEMA_CTS"].strip() or None
                if colunas_c is None:
                    colunas_c = list(m)
                linhas_c.append((x["CTS"], *[m[c] for c in colunas_c]))
                obras_c += [(x["CTS"], nome, un) for nome, un in OBRAS_DA_CTS]
            marc = ", ".join(f"${i + 2}" for i in range(len(colunas_c)))
            await con.executemany(
                f"INSERT INTO input.cts_operacional (cts, {', '.join(colunas_c)}) VALUES ($1, {marc})",
                linhas_c)
            await con.executemany(
                "INSERT INTO input.componentes_cts_capex (cts, componente, unidade) VALUES ($1,$2,$3)",
                obras_c)

        # ---- o retrato ---------------------------------------------------------
        for rot, sql in [
            ("unidades", "SELECT count(*) FROM input.unidade_regional"),
            ("empresas", "SELECT count(*) FROM input.empresa"),
            ("cidades", "SELECT count(*) FROM input.cidade"),
            ("sistemas", "SELECT count(*) FROM input.sistema"),
            ("sistemas em >1 cidade", "SELECT count(*) FROM (SELECT sistema_id FROM input.cidade_sistema GROUP BY 1 HAVING count(*) > 1) t"),
            ("sub-bacias", "SELECT count(*) FROM input.subbacia_operacional"),
            ("coletores", "SELECT count(*) FROM input.cts_operacional"),
            ("macrorregiões (pares)", "SELECT count(*) FROM (SELECT DISTINCT o.sistema_cts, ce.emp_codigo FROM input.cts_operacional o JOIN input.cidade_empresa ce USING (cidade_id) WHERE o.sistema_cts IS NOT NULL) t"),
            ("obras de sub-bacia", "SELECT count(*) FROM input.componentes_subbacias_capex"),
            ("obras de coletor", "SELECT count(*) FROM input.componentes_cts_capex"),
        ]:
            print(f"  {rot:<22} {await con.fetchval(sql)}")
    finally:
        await con.close()


if __name__ == "__main__":
    print(f"carga real -> {BANCO}")
    criar_base_com_o_esquema()
    asyncio.run(carregar())
    print("\npara apontar o serviço para ela: POSTGRES_URL=postgresql://otim:otim@db:5432/" + BANCO)
