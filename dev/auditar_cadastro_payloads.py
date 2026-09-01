"""Audita o contrato real do cadastro contra o que o front consome.

Uso:
    python dev/auditar_cadastro_payloads.py

O script busca os endpoints reais, semeia uma versao pequena do estado do front
e executa os mesmos acessos criticos de `derive()` e das telas.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


BASE = "http://localhost:8000/api"
UID = "uB1"

DB_KEYS = ["fat", "arr", "ligU", "ligA", "ligN", "ligUInd", "ligAInd", "fatInd", "arrInd", "ecoU", "ecoA", "ecoN", "ticket"]
PARAM_KEYS = ["preco", "tarr", "ramp", "vaz", "vazInd", "pot", "popU", "popA"]
OBRA_KEYS = ["nome", "un", "qtd", "preco", "opex", "tPred", "dur", "anoObrig", "proibAte", "wacc"]
OBRA_PEND_KEYS = ["qtd", "preco", "opex", "tPred", "dur", "anoObrig", "proibAte"]
BASE_OBRAS_SUB = [{"nome": "base"}] * 5
BASE_OBRAS_CTS = [{"nome": "base"}] * 4
NUMERO_BR = re.compile(r"^-?\d+(\.\d{3})*(,\d+)?$")


@dataclass
class Issue:
    severity: str
    path: str
    detail: str
    effect: str


def get(path: str) -> Any:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def request(method: str, path: str, body: Any | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8")
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return e.code, parsed


def js_trim(v: Any, path: str) -> str:
    if not isinstance(v, str):
        raise TypeError(f"{path}.trim(): esperado string, recebido {type(v).__name__}={v!r}")
    return v.strip()


def js_lower(v: Any, path: str) -> str:
    if not isinstance(v, str):
        raise TypeError(f"{path}.toLowerCase(): esperado string, recebido {type(v).__name__}={v!r}")
    return v.lower()


def js_index(obj: Any, key: str, path: str) -> Any:
    if obj is None:
        raise TypeError(f"{path}[{key!r}]: Cannot read properties of undefined (reading '{key}')")
    return obj.get(key) if isinstance(obj, dict) else obj[key]


def mk_obras(base: list[dict[str, Any]], override: Any, path: str) -> list[dict[str, Any]]:
    out = []
    for i, b in enumerate(base):
        ov = js_index(override, str(i), path)
        out.append({**b, **(ov or {})})
    return out


def pend_coleta(params: Any, obras: list[dict[str, Any]], por_pop: bool, path: str) -> int:
    n = 0
    keys = PARAM_KEYS[:6] + (PARAM_KEYS[6:] if por_pop else [])
    for k in keys:
        if str((params or {}).get(k, "")).strip() == "":
            n += 1
    for i, o in enumerate(obras):
        for k in OBRA_PEND_KEYS:
            if str(o.get(k)).strip() == "":
                n += 1
    return n


def cidade_por_sub(arvore: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for si, sup in enumerate(arvore):
        for ci, cid in enumerate(sup["cidades"]):
            for ii, sis in enumerate(cid["sistemas"]):
                for sub in sis["subIds"]:
                    out[sub] = cid["id"]
    return out


def regua_de(cob: Any) -> str | None:
    return cob if cob in ("ligacoes", "economias", "populacao") else None


def derive_like_front(state: dict[str, Any]) -> dict[str, Any]:
    cid_sub = state["cidadeDaSub"]
    cidades = state["cidades"]
    pares = state["pares"]

    def regua_sub(sub_id: str) -> str | None:
        cid = cid_sub.get(sub_id)
        c = next((x for x in cidades if x.get("id") == cid), None)
        return regua_de(None if c is None else c.get("cob"))

    def regua_cts(cts_id: str) -> str | None:
        par = next((p for p in pares if p.get("cts") == cts_id), None)
        return regua_sub(par["sub"]) if par else None

    g2 = 0
    for i, c in enumerate(cidades):
        if js_trim(c.get("fim"), f"cidades[{i}].fim") == "":
            g2 += 1
        if js_trim(c.get("cob"), f"cidades[{i}].cob") == "":
            g2 += 1
    for i, m in enumerate(state["metas"]):
        for k in ("cid", "ano", "pct"):
            if js_trim(m.get(k), f"metas[{i}].{k}") == "":
                g2 += 1
    for i, f in enumerate(state["fator"]):
        for k in ("cid", "cob", "par"):
            if js_trim(f.get(k), f"fator[{i}].{k}") == "":
                g2 += 1

    g3 = 0
    for sid, s in state["subs"].items():
        g3 += pend_coleta(s.get("params"), mk_obras(BASE_OBRAS_SUB, s.get("obrasOverride"), f"subs.{sid}.obrasOverride"), regua_sub(sid) == "populacao", f"subs.{sid}")

    g4 = 0
    for i, e in enumerate(state["etes"]):
        keys = ["capMod", "capexMod", "opexMod", "tExec", "capNom", "vazOp", "wacc"]
        if e.get("nova") == "Sim":
            keys += ["terreno", "modulos"]
        for k in keys:
            if str(e.get(k)).strip() == "":
                g4 += 1

    g5 = 0
    for cid, c in state["ctss"].items():
        g5 += pend_coleta(c.get("params"), mk_obras(BASE_OBRAS_CTS, c.get("obrasOverride"), f"ctss.{cid}.obrasOverride"), regua_cts(cid) == "populacao", f"ctss.{cid}")
    return {"g2": g2, "g3": g3, "g4": g4, "g5": g5}


def validate_obj(obj: Any, keys: list[str], path: str, issues: list[Issue], *, strict_number=False) -> None:
    if not isinstance(obj, dict):
        issues.append(Issue("CRIT", path, f"esperado objeto, recebido {type(obj).__name__}", "acessos por propriedade quebram"))
        return
    for k in keys:
        if k not in obj:
            issues.append(Issue("CRIT", f"{path}.{k}", "campo ausente", "pode virar undefined; .trim()/input/toLowerCase quebram conforme uso"))
            continue
        v = obj[k]
        if not isinstance(v, str):
            issues.append(Issue("CRIT", f"{path}.{k}", f"esperado string, recebido {type(v).__name__}={v!r}", ".trim(), input value ou toLowerCase podem quebrar"))
        elif strict_number and v and v != "—" and not NUMERO_BR.match(v):
            issues.append(Issue("WARN", f"{path}.{k}", f"string fora do numero pt-BR estrito: {v!r}", "calculos do front retornam travessao"))


def validate_payloads(payloads: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    sub = payloads["sub"]
    for si, sup in enumerate(sub.get("arvore", [])):
        validate_obj(sup, ["id", "nome"], f"arvore[{si}]", issues)
        for ci, cid in enumerate(sup.get("cidades", [])):
            validate_obj(cid, ["id", "nome"], f"arvore[{si}].cidades[{ci}]", issues)
            for ii, sis in enumerate(cid.get("sistemas", [])):
                validate_obj(sis, ["id", "nome"], f"arvore[{si}].cidades[{ci}].sistemas[{ii}]", issues)
                if not isinstance(sis.get("subIds"), list):
                    issues.append(Issue("CRIT", f"arvore[{si}].cidades[{ci}].sistemas[{ii}].subIds", "esperado array", "rail chama forEach/filter/map"))
    for sid, s in sub.get("subs", {}).items():
        validate_obj(s, ["id", "nome", "sisId", "sistema", "jusante"], f"subs.{sid}", issues)
        validate_obj(s.get("db"), DB_KEYS, f"subs.{sid}.db", issues, strict_number=True)
        validate_obj(s.get("params"), PARAM_KEYS, f"subs.{sid}.params", issues, strict_number=True)
        validate_obras(s.get("obrasOverride"), 5, f"subs.{sid}.obrasOverride", issues)

    cts = payloads["cts"]
    for i, p in enumerate(cts.get("pares", [])):
        validate_obj(p, ["sub", "cts"], f"pares[{i}]", issues)
    for cid, c in cts.get("ctss", {}).items():
        validate_obj(c, ["id", "nome", "subId", "sisId", "sistema", "jusante"], f"ctss.{cid}", issues)
        validate_obj(c.get("db"), DB_KEYS, f"ctss.{cid}.db", issues, strict_number=True)
        validate_obj(c.get("params"), PARAM_KEYS, f"ctss.{cid}.params", issues, strict_number=True)
        validate_obras(c.get("obrasOverride"), 4, f"ctss.{cid}.obrasOverride", issues)

    cont = payloads["contrato"]
    for i, c in enumerate(cont.get("cidades", [])):
        validate_obj(c, ["id", "nome", "fim", "cob"], f"cidades[{i}]", issues)
    for i, m in enumerate(cont.get("metas", [])):
        validate_obj(m, ["cid", "ano", "pct"], f"metas[{i}]", issues)
    for i, f in enumerate(cont.get("fator", [])):
        validate_obj(f, ["cid", "cob", "par"], f"fator[{i}]", issues)

    etes = payloads["etes"]
    for i, e in enumerate(etes.get("etes", [])):
        validate_obj(e, ["id", "sub", "cidId", "nova", "capMod", "capexMod", "opexMod", "tExec", "capNom", "vazOp", "terreno", "modulos", "wacc"], f"etes[{i}]", issues, strict_number=True)

    h = payloads["hier"]
    validate_obj(h.get("unidReg"), ["rid", "rnome", "uid", "unome", "waccMedio"], "unidReg", issues)
    for i, s in enumerate(h.get("empresas", [])):
        validate_obj(s, ["id", "nome"], f"empresas[{i}]", issues)
    for i, c in enumerate(h.get("cidades", [])):
        validate_obj(c, ["id", "nome", "empId"], f"cidadeH[{i}]", issues)
    for i, s in enumerate(h.get("sistemas", [])):
        validate_obj(s, ["id", "nome", "cidId"], f"sistemaH[{i}]", issues)
    for i, t in enumerate(h.get("topo", [])):
        validate_obj(t, ["sis", "id", "nome", "jus"], f"topo[{i}]", issues)

    u = payloads["unidade"]
    validate_obj(u, ["id", "regionalId", "nome", "waccMedio"], "unidade", issues)
    if not isinstance(u.get("resumo"), dict):
        issues.append(Issue("CRIT", "unidade.resumo", "esperado objeto", "selecao de unidade renderiza contadores"))
    else:
        for k in ["cidades", "sistemas", "subBacias", "obras"]:
            if k not in u["resumo"]:
                issues.append(Issue("CRIT", f"unidade.resumo.{k}", "campo ausente", "contador fica undefined"))
    return issues


def validate_obras(override: Any, n: int, path: str, issues: list[Issue]) -> None:
    if not isinstance(override, dict):
        issues.append(Issue("CRIT", path, f"esperado objeto, recebido {type(override).__name__}", "mkObrasDe acessa override['0'] e derruba se vier undefined/null"))
        return
    missing_indices = [str(i) for i in range(n) if str(i) not in override]
    if missing_indices:
        issues.append(Issue("WARN", path, f"indices ausentes {missing_indices}", "front herda BASE_OBRAS; se backend pretendia substituir tudo, dados se perdem"))
    for idx, obra in override.items():
        if idx not in [str(i) for i in range(n)]:
            issues.append(Issue("WARN", f"{path}.{idx}", "indice fora da base", "front ignora porque so percorre a base"))
        if not isinstance(obra, dict):
            issues.append(Issue("CRIT", f"{path}.{idx}", f"esperado objeto, recebido {type(obra).__name__}", "spread de obra quebra"))
            continue
        # O tipo do payload e Record<string, Partial<Obra>>: campos ausentes herdam
        # da base do front. Validamos tipo/formato apenas do que veio.
        for k, v in obra.items():
            if k not in OBRA_KEYS:
                issues.append(Issue("WARN", f"{path}.{idx}.{k}", "campo fora de Obra", "front propaga para o objeto mas nao usa"))
                continue
            if not isinstance(v, str):
                issues.append(Issue("CRIT", f"{path}.{idx}.{k}", f"esperado string, recebido {type(v).__name__}={v!r}", ".trim()/input/calculo pode quebrar"))
            elif k not in ("nome", "un") and v and not NUMERO_BR.match(v):
                issues.append(Issue("WARN", f"{path}.{idx}.{k}", f"string fora do numero pt-BR estrito: {v!r}", "calculos do front retornam travessao"))


def state_from(payloads: dict[str, Any]) -> dict[str, Any]:
    return {
        "subs": deepcopy(payloads["sub"]["subs"]),
        "cidadeDaSub": cidade_por_sub(payloads["sub"]["arvore"]),
        "cidades": deepcopy(payloads["contrato"]["cidades"]),
        "metas": deepcopy(payloads["contrato"]["metas"]),
        "fator": deepcopy(payloads["contrato"]["fator"]),
        "etes": deepcopy(payloads["etes"]["etes"]),
        "hier": deepcopy(payloads["hier"]),
        "ctss": deepcopy(payloads["cts"]["ctss"]),
        "pares": deepcopy(payloads["cts"]["pares"]),
    }


def main() -> None:
    payloads = {
        "cts": get(f"/unidades/{UID}/cts"),
        "sub": get(f"/unidades/{UID}/sub-bacias"),
        "contrato": get(f"/unidades/{UID}/contrato"),
        "etes": get(f"/unidades/{UID}/etes"),
        "hier": get(f"/unidades/{UID}/hierarquia"),
        "unidade": get(f"/unidades/{UID}"),
    }
    print(f"GET: {len(payloads['sub']['subs'])} subs, {len(payloads['cts']['ctss'])} cts, {len(payloads['contrato']['cidades'])} cidades, {len(payloads['etes']['etes'])} etes")
    print("derive(GET):", derive_like_front(state_from(payloads)))

    issues = validate_payloads(payloads)
    print("\nDIVERGENCIAS DE LEITURA:")
    for i in issues[:80]:
        print(f"{i.severity} {i.path}: {i.detail} -> {i.effect}")
    if len(issues) > 80:
        print(f"... +{len(issues)-80} divergencias")

    print("\nESCRITA:")
    first_sub_id, first_sub = next(iter(payloads["sub"]["subs"].items()))
    body_sub = {"params": first_sub["params"], "db": first_sub["db"], "obrasOverride": first_sub["obrasOverride"]}
    print("PUT sub:", first_sub_id, request("PUT", f"/unidades/{UID}/sub-bacias/{first_sub_id}", body_sub)[0])

    first_cts_id, first_cts = next(iter(payloads["cts"]["ctss"].items()))
    body_cts = {"params": first_cts["params"], "db": first_cts["db"], "obrasOverride": first_cts["obrasOverride"]}
    print("PUT cts:", first_cts_id, request("PUT", f"/unidades/{UID}/cts/{first_cts_id}", body_cts)[0])

    first_ete = payloads["etes"]["etes"][0]
    print("PUT ete:", first_ete["id"], request("PUT", f"/unidades/{UID}/etes/{first_ete['id']}", {"ete": first_ete})[0])

    first_cid = payloads["contrato"]["cidades"][0]
    body_cid = {
        "cidade": first_cid,
        "metas": [m for m in payloads["contrato"]["metas"] if m["cid"] == first_cid["id"]],
        "fator": [f for f in payloads["contrato"]["fator"] if f["cid"] == first_cid["id"]],
    }
    print("PUT cidade:", first_cid["id"], request("PUT", f"/unidades/{UID}/contrato/{first_cid['id']}", body_cid)[0])

    paired = {p["sub"] for p in payloads["cts"]["pares"]}
    sub_id = next(sid for sid in payloads["sub"]["subs"] if sid not in paired)
    new_cts = {
        **deepcopy(first_cts),
        "id": f"audit_{sub_id}",
        "nome": f"audit_{sub_id}",
        "subId": sub_id,
        "sisId": payloads["sub"]["subs"][sub_id]["sisId"],
        "sistema": payloads["sub"]["subs"][sub_id]["sistema"],
        "jusante": payloads["sub"]["subs"][sub_id]["jusante"],
        "obrasOverride": {},
    }
    status, post = request("POST", f"/unidades/{UID}/cts", {"subId": sub_id, "cts": new_cts})
    print("POST cts:", status, "top-level keys:", sorted(post.keys()) if isinstance(post, dict) else type(post).__name__)
    try:
        bad_state = state_from(payloads)
        # Isto simula exatamente o tipo declarado por useCriarCts: o front espera
        # que `post` seja a Cts, mas o backend atual devolve {par, cts}.
        cts_obj = post
        if cts_obj.get("subId") != sub_id:
            cts_obj = {**cts_obj, "subId": sub_id}
        bad_state["ctss"][cts_obj.get("id")] = cts_obj
        bad_state["pares"].append({"sub": sub_id, "cts": cts_obj.get("id")})
        print("derive(apos POST adotado pelo front):", derive_like_front(bad_state))
    except Exception as e:
        print("CRASH REPRODUZIDO:", e)
    finally:
        request("DELETE", f"/unidades/{UID}/cts/audit_{sub_id}")


if __name__ == "__main__":
    main()
