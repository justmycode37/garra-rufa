"""HTTP clients for Monarch, Orphadata, and Open Targets (one anchor at a time)."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from garra.sources.envelope import now_iso
from garra.ui.monarch import MonarchClient

MONARCH = "https://api-v3.monarchinitiative.org/v3/api"
MONARCH_SEMSIM = "https://api.monarchinitiative.org/v3/api/semsim/search"
ORPHADATA = "https://api.orphadata.com"
ORPHACODE = "https://api.orphacode.org/EN/ClinicalEntity"
OT_GRAPHQL = "https://api.platform.opentargets.org/api/v4/graphql"

USER_AGENT = "garra-rufa-packet/1"
MAX_BYTES = 6_000_000
DP = "biolink:DiseaseToPhenotypicFeatureAssociation"
CAUSAL = "biolink:CausalGeneToDiseaseAssociation"

TARGET_PATHWAYS = """
query($id:String!){ target(ensemblId:$id){ id approvedSymbol
 pathways{pathwayId pathway} }}"""

MAP_IDS = """
query($terms:[String!]!){ mapIds(queryTerms:$terms, entityNames:["target"]){
 mappings{ term hits{id name entity} } } }"""


def _get_json(url: str, *, params: dict | None = None, headers: dict | None = None) -> dict:
    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    h = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    with urlopen(Request(url, headers=h), timeout=45) as resp:
        raw = resp.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("response too large")
    return json.loads(raw)


def _post_json(url: str, body: dict, headers: dict | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        **(headers or {}),
    }
    req = Request(url, data=json.dumps(body).encode(), headers=h, method="POST")
    with urlopen(req, timeout=60) as resp:
        raw = resp.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("response too large")
    return json.loads(raw)


def monarch_search_disease(query: str, *, limit: int = 8) -> dict:
    data = _get_json(f"{MONARCH}/search", params={"q": query, "limit": limit})
    items = data.get("items") or []
    for it in items:
        if (it.get("id") or "").startswith("MONDO:"):
            return {"retrieved_at": now_iso(), "query": query, "item": it, "items": items}
    if items:
        return {"retrieved_at": now_iso(), "query": query, "item": items[0], "items": items}
    raise LookupError(f"No Monarch search hit for {query!r}")


def monarch_entity(mondo_id: str) -> dict:
    return {"retrieved_at": now_iso(), "entity": _get_json(f"{MONARCH}/entity/{quote(mondo_id, safe='')}")}


def monarch_disease_phenotypes(mondo_id: str, *, limit: int = 200) -> dict:
    data = _get_json(
        f"{MONARCH}/association",
        params={"subject": mondo_id, "category": DP, "limit": limit, "direct": "true"},
    )
    hps: list[str] = []
    for it in data.get("items") or []:
        hp = it.get("object")
        if isinstance(hp, str) and hp.startswith("HP:"):
            hps.append(hp)
    return {"retrieved_at": now_iso(), "mondo_id": mondo_id, "hpo_ids": sorted(set(hps)), "raw": data}


def monarch_disease_genes(mondo_id: str, *, limit: int = 50) -> dict:
    data = _get_json(
        f"{MONARCH}/association",
        params={"object": mondo_id, "category": CAUSAL, "limit": limit},
    )
    symbols: list[str] = []
    for it in data.get("items") or []:
        sym = it.get("subject_label") or it.get("subject")
        if isinstance(sym, str) and sym and not sym.startswith("HGNC:"):
            symbols.append(sym)
        elif isinstance(it.get("subject"), str) and it["subject"].startswith("HGNC:"):
            label = it.get("subject_label")
            if label:
                symbols.append(label)
    return {
        "retrieved_at": now_iso(),
        "mondo_id": mondo_id,
        "gene_symbols": sorted(set(symbols)),
        "raw": data,
    }


def monarch_semsim(hpo_ids: list[str], *, limit: int = 15) -> dict:
    if not hpo_ids:
        raise ValueError("Need at least one HPO id for Monarch semsim")
    client = MonarchClient()
    out = client.search(hpo_ids, "jaccard_similarity", limit=limit)
    return {"retrieved_at": out["retrieved_at"], "request": out["request"], "results": out["results"]}


def orphadata_phenotypes(orpha_code: str) -> dict:
    code = orpha_code.replace("ORPHA:", "").replace("Orphanet:", "")
    url = f"{ORPHADATA}/rd-phenotypes/orphacodes/{code}"
    data = _get_json(url, headers={"apiKey": "orphadata"})
    hps: list[str] = []
    results = (data.get("data") or {}).get("results")
    if isinstance(results, dict):
        assoc = (results.get("Disorder") or {}).get("HPODisorderAssociation") or []
        for row in assoc:
            freq = (row.get("HPOFrequency") or "").lower()
            if freq.startswith("excluded"):
                continue
            hp = (row.get("HPO") or {}).get("HPOId")
            if hp:
                hps.append(hp if hp.startswith("HP:") else f"HP:{hp}")
    return {"retrieved_at": now_iso(), "orpha": f"ORPHA:{code}", "hpo_ids": sorted(set(hps)), "raw": data}


def orphadata_genes(orpha_code: str) -> dict:
    code = orpha_code.replace("ORPHA:", "").replace("Orphanet:", "")
    url = f"{ORPHADATA}/rd-associated-genes/orphacodes/{code}"
    data = _get_json(url, headers={"apiKey": "orphadata"})
    symbols: list[str] = []
    results = (data.get("data") or {}).get("results")
    if isinstance(results, dict):
        for row in results.get("DisorderGeneAssociation") or []:
            sym = (row.get("Gene") or {}).get("Symbol")
            if sym:
                symbols.append(str(sym).upper())
    return {"retrieved_at": now_iso(), "orpha": f"ORPHA:{code}", "gene_symbols": sorted(set(symbols)), "raw": data}


def orpha_from_mondo_entity(entity: dict) -> str | None:
    for xref in entity.get("xref") or []:
        if not isinstance(xref, str):
            continue
        u = xref.upper()
        if u.startswith("ORPHA:"):
            return "ORPHA:" + xref.split(":", 1)[1]
        if u.startswith("ORPHANET:"):
            return "ORPHA:" + xref.split(":", 1)[1]
    return None


def opentargets_pathways_for_symbol(symbol: str) -> dict:
    mapped = _post_json(OT_GRAPHQL, {"query": MAP_IDS, "variables": {"terms": [symbol]}})
    hits = (
        ((mapped.get("data") or {}).get("mapIds") or {}).get("mappings") or [{}]
    )[0].get("hits") or []
    ensembl = None
    for hit in hits:
        if hit.get("entity") == "target" and hit.get("id", "").startswith("ENSG"):
            ensembl = hit["id"]
            break
    if not ensembl:
        return {"retrieved_at": now_iso(), "symbol": symbol, "pathway_ids": [], "pathways": []}
    body = _post_json(
        OT_GRAPHQL, {"query": TARGET_PATHWAYS, "variables": {"id": ensembl}}
    )
    target = (body.get("data") or {}).get("target") or {}
    pathways = target.get("pathways") or []
    ids = [p.get("pathwayId") for p in pathways if p.get("pathwayId")]
    return {
        "retrieved_at": now_iso(),
        "symbol": symbol,
        "ensembl_id": ensembl,
        "pathway_ids": ids,
        "pathways": pathways,
    }


def unavailable_message(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, (URLError, TimeoutError, OSError)):
        return str(getattr(exc, "reason", exc))
    return str(exc)
