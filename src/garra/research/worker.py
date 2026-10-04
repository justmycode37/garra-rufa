"""Interactive adapter for src/query-test and its separate literature pipeline.

The existing adapters prefer downloaded local indexes, then their live providers.
Each worker has a total deadline and returns provenance, partial failures, and real
records. It never reads webapp accounts, uploads, or private workspace records.
"""
import contextlib
import hashlib
import html
import importlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src" / "query-test"))


def clean(value, limit=4000):
    return html.unescape(re.sub(r"<[^>]*>", "", str(value or "")))[:limit].strip()


def safe_url(value):
    try:
        u = urlsplit(str(value or ""))
        return str(value) if u.scheme in ("http", "https") and u.netloc and not u.username and not u.password else None
    except ValueError:
        return None


def entity_url(node):
    details = node.get("info") or {}
    # Entities.merge_info stores these fields by provider, not as Node.info scalars.
    for value in [details.get("url"), *(details.get("urls") or {}).values()]:
        direct = safe_url(value)
        if direct:
            return direct
    curie = node["id"]
    prefix, _, value = curie.partition(":")
    bases = {"MONDO": "https://monarchinitiative.org/MONDO:",
             "HP": "https://hpo.jax.org/browse/term/HP:",
             "ORPHA": "https://www.orpha.net/en/disease/detail/",
             "OMIM": "https://omim.org/entry/", "NCT": "https://clinicaltrials.gov/study/",
             "HGNC": "https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/HGNC:",
             "NCBIGene": "https://www.ncbi.nlm.nih.gov/gene/"}
    return bases[prefix] + quote(value, safe="") if prefix in bases else None


def bound_requests(obj, deadline, stats=None):
    request = obj.session.request

    def bounded(method, url, *args, **kwargs):
        remaining = deadline - time.monotonic()
        if remaining < .2:
            raise TimeoutError("Research deadline reached")
        kwargs["timeout"] = max(.2, min(8, remaining))
        response = request(method, url, *args, **kwargs)
        if "text/html" in response.headers.get("Content-Type", "") and any(
            marker in response.text.lower() for marker in
            ("vérification de la connexion", "checking your browser", "verify you are human", "just a moment...")
        ):
            raise RuntimeError("Provider requires interactive access")
        return response

    obj.session.request = bounded
    if stats:
        stats.instrument(obj)


def graph(query, category, deadline):
    from main import run, parse_input, build_graph
    from entities import Entities
    from report import Stats
    from sources import _MODULES
    from sources import _groups
    from dataclasses import replace

    names = ["mondo", "monarch", "clinicaltrials"]
    if category == "contacts":
        names = ["mondo", "orphanet_groups", "ern", "clinicaltrials"]
    sources, stats, ents = [], Stats(), Entities()
    for name in names:
        module = importlib.import_module("sources." + name)
        source = getattr(module, _MODULES[name])()
        if name in {"orphanet_groups", "ern"}:
            original_query = source.query

            def with_aliases(node, limit=10, original=original_query):
                # A merged MONDO entity can have several ORPHA identifiers. An
                # empty first identifier must not hide resources on another alias.
                codes = _groups.orpha_codes(node)[:3]
                if not codes:
                    return original(node, limit=limit)
                for code in codes:
                    found = original(replace(node, id="ORPHA:" + code, xrefs=()), limit=min(limit, 5))
                    if found:
                        return found
                return []

            source.query = with_aliases
        bound_requests(source, deadline, stats)
        sources.append(source)
    start = parse_input(query, None)
    edges = run(start, sources, 3 if category == "contacts" else 2, 5, 2, ents, stats, focus_limit=12)
    g = build_graph(edges, ents)
    data = {
        "start": ents.key(ents.find(start.key())),
        "focus": [ents.key(ents.find(f)) for f in stats.focus],
        "nodes": [{"id": key, "label": value["label"], "kind": value["kind"],
                   "sources": sorted(value["sources"]), "xrefs": sorted(value["xrefs"]),
                   "info": value.get("info", {})} for key, value in g.nodes(data=True)],
        "edges": [{"from": u, "to": v, "relation": value["relation"],
                   "source": value["source"], "evidence": sorted(value.get("evidence", []))}
                  for u, v, value in g.edges(data=True)],
    }
    failed = [name for name, value in stats.src.items() if value["http_errors"] or value["raised"]]
    if "orphanet_groups" in failed and "ern" not in failed:
        failed.append("ern")  # Disease-to-ERN resolution uses the same Orphanet pages.
    succeeded = [name for name, value in stats.src.items()
                 if value["calls"] and (value["edges"] or not value["http_errors"])]
    return data, failed, succeeded


def graph_sources(data, limit, retrieved, contacts=False):
    result = []
    focus = set(data.get("focus", []))
    contact_kinds = {"expert_centre", "healthcare_provider", "hospital", "doctor", "clinician", "investigator"}
    organisation_kinds = {"organisation", "organization", "patient_organisation", "expert_network",
                          "research_network", "consortium", "network", "registry", "biobank"}
    direct = {e["to"] for e in data["edges"] if e["from"] in focus} | {e["from"] for e in data["edges"] if e["to"] in focus}
    resource_ids = {n["id"] for n in data["nodes"] if n["kind"] in contact_kinds | organisation_kinds and n["id"] in direct}
    reachable_resources = direct | {e["to"] for e in data["edges"] if e["from"] in resource_ids}
    nodes = sorted(data["nodes"], key=lambda n: (n["id"] not in focus,
                    n["kind"] not in contact_kinds | organisation_kinds, n["id"]))
    for node in nodes:
        if contacts and (node["kind"] not in contact_kinds | organisation_kinds | {"clinical_trial"} or node["id"] not in reachable_resources):
            continue
        if node["kind"] in {"term", "unknown"}:
            continue
        url = entity_url(node)
        if not url:
            continue
        relations = [e for e in data["edges"] if e["to"] == node["id"] or e["from"] == node["id"]]
        labels = {n["id"]: n["label"] for n in data["nodes"]}
        facts = [f"{labels.get(e['from'], e['from'])} — {e['relation'].replace('_', ' ')} — "
                 f"{labels.get(e['to'], e['to'])}" for e in relations[:5]]
        details = node.get("info") or {}
        kind = "contact" if node["kind"] in contact_kinds else "organization" if node["kind"] in organisation_kinds else "trial" if node["kind"] == "clinical_trial" else "reference"
        result.append({"id": "repo-" + hashlib.sha256(node["id"].encode()).hexdigest()[:24],
                       "title": clean(node["label"], 500), "url": url, "kind": kind,
                       "excerpt": clean(details.get("description") or
                                        next(iter((details.get("descriptions") or {}).values()), None) or ". ".join(facts)),
                       "providers": node.get("sources", []), "entityId": node["id"],
                       "recordType": node["kind"].replace("_", " "), "retrievedAt": retrieved,
                       "relations": facts, "email": clean(details.get("email"), 254) or None,
                       "phone": clean(details.get("phone"), 100) or None,
                       "location": clean(details.get("country") or details.get("location"), 300) or None})
        if len(result) >= limit:
            break
    return result


def papers(query, limit, deadline, retrieved):
    from literature import load_providers
    from literature.base import Cache, Collection, Concept, Query, Paper, Hit
    from literature.harvest import harvest
    from literature.plan import plan, MeshLookup
    import literature.base as base

    # Interactive requests use the same providers, planner, harvest and deduper as
    # literature/main.py, with bounded result caps instead of the bulk CLI's 3000.
    base.RETRIES = 0
    cache_dir = Path(os.environ.get("GARRA_RESEARCH_CACHE_DIR", ROOT / "data" / "literature-cache"))
    cache = Cache(cache_dir / "web.sqlite")
    with cache._lock:
        cache.db.execute("DELETE FROM r WHERE t < ?", (time.time() - 86400,))
        cache.db.commit()
    provider_names = ["pubmed", "europepmc", "pubtator", "litvar"]
    providers = load_providers(cache, provider_names)
    for provider in providers:
        bound_requests(provider, deadline)
    mesh = MeshLookup(cache)
    bound_requests(mesh, deadline)
    # A name search remains possible even when graph resolution is unavailable.
    concept = Concept("term:" + query, query, "disease", [query])
    queries = [Query("web-disease", "disease", concept, cap=limit)]
    collection = Collection()
    pmid = re.fullmatch(r"(?:PMID\s*:?\s*)?(\d{1,10})", query, re.IGNORECASE)
    variant = re.fullmatch(r"rs\d+", query, re.IGNORECASE)
    if pmid:
        collection.add(Paper(pmid=pmid[1], hits=[Hit("pubmed", query, "PMID:" + pmid[1], "identifier")]))
        queries = []
    elif variant:
        queries = [Query("web-variant", "variant", None,
                         Concept(query, query, "variant", [query], rsid=query.lower()), cap=limit)]
    # Reuse a graph produced by the graph pipeline, if available. Never infer a
    # disease from an unrelated cached graph or a file name supplied by the client.
    graph_path = cache_dir / ("graph-" + hashlib.sha256(query.casefold().encode()).hexdigest() + ".json")
    if not pmid and not variant and graph_path.exists() and time.time() - graph_path.stat().st_mtime < 3600:
        data = json.loads(graph_path.read_text())
        for p in harvest(data):
            collection.add(p)
        planned = plan(data, mesh)
        if planned:
            queries = planned[:3]
            for q in queries:
                q.cap = min(limit, q.cap)
    attempted = []
    skipped = set()
    for q in queries:
        for provider in providers:
            if not provider.handles(q):
                continue
            if time.monotonic() >= deadline:
                skipped.add(provider.name)
                continue
            attempted.append(provider.name)
            _, results = provider.search(q)
            for paper in results:
                collection.add(paper)
    for name in ("europepmc", "pubmed"):
        provider = next((p for p in providers if p.name == name), None)
        if provider and time.monotonic() < deadline:
            attempted.append(name)
            provider.enrich(collection)
        elif provider:
            skipped.add(name)
    failed = sorted({p.name for p in providers if p.errors} | skipped |
                    (set(provider_names) - {p.name for p in providers}))
    succeeded = sorted({p.name for p in providers if p.name in attempted and not p.errors})
    sources = []
    for p in sorted(collection.papers, key=lambda p: (-len({h.provider for h in p.hits}),
                                                     min((h.rank or 0 for h in p.hits), default=0))):
        if not p.title:
            continue  # An unresolved PMID alone is not a verified paper card.
        url = ("https://pubmed.ncbi.nlm.nih.gov/" + p.pmid + "/") if p.pmid else ("https://doi.org/" + quote(p.doi, safe="/")) if p.doi else "https://pmc.ncbi.nlm.nih.gov/articles/" + p.pmcid + "/"
        sources.append({"id": "paper-" + hashlib.sha256(p.ids()[0].encode()).hexdigest()[:24],
                        "title": clean(p.title, 2000), "url": url, "kind": "paper",
                        "excerpt": clean(p.abstract) or "Abstract unavailable from the source.",
                        "year": str(p.year) if p.year else None, "journal": clean(p.journal, 300) or None,
                        "authors": p.authors[:12], "doi": p.doi, "pmid": p.pmid,
                        "providers": sorted({h.provider for h in p.hits}),
                        "openAccess": p.open_access, "preprint": p.preprint,
                        "retrievedAt": retrieved})
        if len(sources) >= limit:
            break
    cache.db.close()
    return sources, failed, succeeded


def research(payload):
    query, limit = payload["query"], payload["limit"]
    retrieved = datetime.now(timezone.utc).isoformat()
    deadline = time.monotonic() + 52
    if payload["mode"] == "papers":
        sources, failed, succeeded = papers(query, limit, deadline, retrieved)
        pipeline = "repository-literature"
    else:
        data, failed, succeeded = graph(query, payload["category"], deadline)
        sources = graph_sources(data, limit, retrieved, contacts=payload["category"] == "contacts")
        cache_dir = Path(os.environ.get("GARRA_RESEARCH_CACHE_DIR", ROOT / "data" / "literature-cache"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        graph_path = cache_dir / ("graph-" + hashlib.sha256(query.casefold().encode()).hexdigest() + ".json")
        if data["focus"]:
            temporary = graph_path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(data, default=sorted))
            temporary.replace(graph_path)
        pipeline = "repository-graph"
    status = "partial" if sources and failed else "ok" if sources else "unavailable" if failed else "empty"
    return {"status": status, "query": query, "sources": sources, "pipeline": pipeline,
            "providers": succeeded, "unavailableProviders": failed, "retrievedAt": retrieved,
            "cached": False, "notice": "Research evidence, not a diagnosis or a recommendation of a clinician."}


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = research(payload)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
