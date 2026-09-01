"""Tira do banco as rodadas que nasceram de teste, e só elas.

## O que isto é, e o que NÃO é

É higiene de ambiente, não requisito de produto. Por isso vive em `dev/`, como
script explícito, e **nada disto vira lógica de aplicação**: o serviço não tem —
e não pode ter — regra que decide sozinho que uma rodada "é de teste". Rodada é
imutável (R4); a única exclusão que o produto oferece é a que um humano pede,
uma por vez, pelo `DELETE /runs/{id}`.

O problema que ele resolve: o histórico e a auditoria misturam rodadas de gente
com rodadas de smoke, de seed sintético e de execuções que morreram em `ERRO`.
Quem abre a tela de histórico não distingue, e quem for validar a dedupe de
rodada concluída tropeça num `SUCESSO` que nunca publicou resultado.

## As regras, uma por linha, e por que cada uma

Uma rodada é candidata quando bate em QUALQUER destas. Todas são estreitas de
propósito — a lista é o contrato, e ampliá-la é decisão de quem lê, não do script:

  `unidade` sintética     `u1`/`u_par` vêm do `dev/legado_seed/`. Não existem em
                          `input` depois de um `recarregar_tudo.py`: são rodadas
                          sobre um cadastro que não existe mais.
  identidade de teste     `smoke`, `u1`, `u_par` como autor. Não são pessoas.
  `ERRO`                  nunca produziu resultado. Ocupa o histórico afirmando
                          uma tentativa que não tem o que mostrar.
  `SUCESSO` órfão         diz que deu certo e não há linha em `public.otim_meta`.
                          É o estado que mente, e é o que polui a dedupe de
                          rodada concluída: ela procura publicação, e acha um
                          sucesso sem resultado.
  repetição do laço       a MESMA rodada disparada dezenas de vezes enquanto se
                          mexia na tela — mesmo autor, mesma regional, mesmo
                          rótulo. Guarda a mais recente de cada grupo.

**Só a última apaga RESULTADO**, e por isso ela é a mais estreita: exige os três
campos iguais. Uma rodada com rótulo próprio nunca cai nela — foi nomeada, alguém
quis distingui-la.

**`dev@local` não é critério**, e a ausência é deliberada. É o usuário que o
serviço assume quando a autenticação está desligada — ou seja, é a identidade de
QUALQUER pessoa em desenvolvimento, inclusive nas rodadas boas. Apagar por ela
levaria junto o resultado que a tela mostra, e deixaria unidades sem nenhuma
rodada. O que denuncia o laço é o rótulo repetido, não quem o disparou.

## Como usar

    python dev/limpar_rodadas_de_teste.py            # só mostra o que faria
    python dev/limpar_rodadas_de_teste.py --apagar   # apaga

Sem `--apagar` ele não escreve nada. A ordem de exclusão é a mesma do
`resultado.excluir` (`otim_meta` primeiro, que cascateia para as 13 tabelas de
resultado, depois a fila), para não haver dois jeitos de apagar uma rodada.

**Rode depois dos smokes.** O `testes_de_integracao/smoke.py` termina com um `POST /runs` que, sem
Service Bus configurado, responde 503 — e deixa uma rodada em `ERRO` para trás, a
cada execução. É comportamento correto do serviço (o pedido foi registrado antes
de a fila falhar), mas significa que o histórico ganha uma linha inútil toda vez
que alguém roda o smoke.
"""

import asyncio
import os
import sys

os.environ.setdefault("POSTGRES_URL", "postgresql://otim:otim@localhost:55432/otimizador")
os.environ.setdefault("SERVICE_BUS_CONN", "")
sys.path.insert(0, ".")

from app.infra import db  # noqa: E402

#: Unidades que só existem no seed sintético (`dev/legado_seed/seed.sql`).
UNIDADES_DE_TESTE = ("u1", "u_par")

#: Autores que não são pessoas. `dev@local` fica de fora — ver o topo do arquivo.
AUTORES_DE_TESTE = ("smoke", "u1", "u_par")

#: A consulta é a ÚNICA fonte da classificação: o que ela devolve é o que sai, e
#: o motivo viaja junto para o relatório poder ser lido sem consultar o código.
CANDIDATAS = """
SELECT r.run_id,
       r.unidade,
       r.solicitado_por,
       COALESCE(s.status, '—') AS status,
       EXISTS(SELECT 1 FROM public.otim_meta m WHERE m.run_id = r.run_id) AS publicada,
       CASE
         WHEN r.unidade = ANY($1)         THEN 'unidade sintética do seed'
         WHEN r.solicitado_por = ANY($2)  THEN 'autor não é pessoa'
         WHEN s.status = 'ERRO'           THEN 'ERRO: nunca produziu resultado'
         ELSE 'SUCESSO sem publicação: diz que deu certo e não há resultado'
       END AS motivo
  FROM controle.run_request r
  LEFT JOIN controle.run_status s USING (run_id)
 WHERE r.unidade = ANY($1)
    OR r.solicitado_por = ANY($2)
    OR s.status = 'ERRO'
    OR (s.status = 'SUCESSO'
        AND NOT EXISTS (SELECT 1 FROM public.otim_meta m WHERE m.run_id = r.run_id))
 ORDER BY r.solicitado_em
"""

#: Rodada PUBLICADA de unidade/autor de teste que nem pedido na fila tem. Existe
#: porque `rodar_simulacao_real.py` publica direto, sem passar pela fila: a
#: consulta acima nunca a alcançaria.
PUBLICADAS_ORFAS = """
SELECT m.run_id, m.regional, m.usuario, m.rotulo
  FROM public.otim_meta m
 WHERE m.usuario = ANY($1)
   AND NOT EXISTS (SELECT 1 FROM controle.run_request r WHERE r.run_id = m.run_id)
 ORDER BY m.data_hora
"""

#: As repetições do laço de desenvolvimento: a MESMA rodada disparada dezenas de
#: vezes enquanto se mexia na tela, todas com o mesmo rótulo e a mesma regional
#: (`uA2 — pela tela`, dez vezes em uma tarde).
#:
#: Guarda a MAIS RECENTE de cada grupo, e não a primeira: é a que rodou contra o
#: cadastro mais parecido com o de hoje. E guardar UMA, em vez de nenhuma, é o
#: que mantém a tela de resultados com o que mostrar em cada unidade.
#:
#: Esta regra é a única aqui que apaga RESULTADO de verdade, e por isso é a mais
#: estreita das quatro: exige rótulo repetido, mesma regional e mesmo autor. Uma
#: rodada com rótulo próprio nunca cai nela — foi nomeada, alguém quis distingui-la.
DUPLICATAS_DO_LACO = """
SELECT run_id, regional, usuario, rotulo, quantas
  FROM (
    SELECT m.run_id, m.regional, m.usuario, m.rotulo, m.data_hora,
           row_number() OVER (PARTITION BY m.usuario, m.regional, m.rotulo
                                  ORDER BY m.data_hora DESC) AS posicao,
           count(*)     OVER (PARTITION BY m.usuario, m.regional, m.rotulo) AS quantas
      FROM public.otim_meta m
  ) t
 WHERE posicao > 1
 ORDER BY usuario, regional, data_hora
"""


async def main() -> int:
    apagar = "--apagar" in sys.argv
    await db.abrir_pool()
    try:
        candidatas = await db.buscar(CANDIDATAS, list(UNIDADES_DE_TESTE), list(AUTORES_DE_TESTE))
        orfas = await db.buscar(PUBLICADAS_ORFAS, list(AUTORES_DE_TESTE))
        duplicatas = await db.buscar(DUPLICATAS_DO_LACO)

        antes = await _contar()
        print(f"fila: {antes['fila']} rodadas · publicadas: {antes['publicadas']}\n")

        if not candidatas and not orfas and not duplicatas:
            print("Nada a limpar: nenhuma rodada bate nas regras.")
            return 0

        print(f"{'run_id':<28} {'unidade':<8} {'autor':<14} {'status':<9} pub  motivo")
        print("-" * 108)
        for c in candidatas:
            pub = "sim" if c["publicada"] else "não"
            print(
                f"{c['run_id']:<28} {c['unidade']:<8} {c['solicitado_por']:<14} "
                f"{c['status']:<9} {pub:<4} {c['motivo']}"
            )
        for o in orfas:
            print(f"{o['run_id']:<28} {'—':<8} {o['usuario']:<14} {'—':<9} {'sim':<4} publicada por autor de teste")
        for d in duplicatas:
            print(
                f"{d['run_id']:<28} {'—':<8} {d['usuario']:<14} {'—':<9} {'sim':<4} "
                f"repetição de {d['rotulo']!r} ({d['quantas']}x; fica a mais recente)"
            )

        ids = (
            [c["run_id"] for c in candidatas]
            + [o["run_id"] for o in orfas]
            + [d["run_id"] for d in duplicatas]
        )
        com_resultado = (
            sum(1 for c in candidatas if c["publicada"]) + len(orfas) + len(duplicatas)
        )
        print(f"\n{len(ids)} rodada(s), {com_resultado} com resultado publicado a apagar junto.")

        if not apagar:
            print("\nNada foi apagado. Rode com `--apagar` para executar.")
            return 0

        async with db.transacao() as con:
            # Mesma ordem do `resultado.excluir`: o `otim_meta` cascateia para as
            # 13 tabelas de resultado, e a fila sai depois.
            n_meta = await con.execute(
                "DELETE FROM public.otim_meta WHERE run_id = ANY($1)", ids
            )
            await con.execute("DELETE FROM controle.run_diagnostico WHERE run_id = ANY($1)", ids)
            await con.execute("DELETE FROM controle.run_status WHERE run_id = ANY($1)", ids)
            await con.execute("DELETE FROM controle.run_request WHERE run_id = ANY($1)", ids)

        depois = await _contar()
        print(f"\napagado. {n_meta}")
        print(f"fila: {antes['fila']} -> {depois['fila']} · publicadas: "
              f"{antes['publicadas']} -> {depois['publicadas']}")
        return 0
    finally:
        await db.fechar_pool()


async def _contar() -> dict[str, int]:
    linha = await db.buscar_um(
        """SELECT (SELECT count(*) FROM controle.run_request)  AS fila,
                  (SELECT count(*) FROM public.otim_meta)      AS publicadas"""
    )
    return {k: int(v) for k, v in (linha or {}).items()}


raise SystemExit(asyncio.run(main()))
