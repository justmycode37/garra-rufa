"""One end-to-end path: resolve → neighbors → live research signals."""

from __future__ import annotations

import re

from garra.graph.resolve import resolve_query
from garra.similarity import similar_diseases
from garra.sources.queries import search_clinicaltrials, search_pubmed, search_reporter


def _search_terms(query: str, resolved: dict) -> list[str]:
    terms = [query.strip()]
    primary = resolved.get("primary") or {}
    if primary.get("kind") == "disease" and primary.get("name"):
        terms.append(primary["name"])
    for match in resolved.get("matches") or []:
        if match.get("type") == "disease" and match.get("name"):
            terms.append(match["name"])
        if match.get("type") == "gene" and match.get("symbol"):
            terms.append(match["symbol"])
    return list(dict.fromkeys(t for t in terms if t))


def _condition_matches(record: dict, allow_tokens: set[str]) -> bool:
    blob = " ".join(record.get("conditions") or []).lower()
    title = (record.get("title") or "").lower()
    for token in allow_tokens:
        if token in blob or token in title:
            return True
    return False


def filter_trials(trials_payload: dict, *, allow_tokens: set[str]) -> dict:
    kept = []
    dropped = []
    for record in trials_payload.get("records") or []:
        if _condition_matches(record, allow_tokens):
            kept.append(record)
        else:
            dropped.append({**record, "warning": "TEXT_MISMATCH"})
    out = dict(trials_payload)
    out["records"] = kept
    out["filtered_out"] = dropped
    if dropped:
        out["warnings"] = ["TEXT_MISMATCH"]
    return out


def build_journey(query: str, *, neighbor_limit: int = 5, live: bool = True) -> dict:
    resolved = resolve_query(query)
    if resolved["status"] != "ok":
        return {"status": resolved["status"], "query": query, "resolve": resolved, "steps": []}

    primary = resolved["primary"]
    if primary.get("kind") == "gene":
        similar = similar_diseases(gene_key=primary["gene_key"], limit=neighbor_limit)
    else:
        similar = similar_diseases(disease_key=primary["disease_key"], limit=neighbor_limit)

    if similar.get("status") == "ambiguous":
        return {
            "status": "ambiguous",
            "query": query,
            "resolve": resolved,
            "similar_diseases": similar,
            "steps": [],
        }

    terms = _search_terms(query, resolved)
    search_label = terms[0]
    allow_tokens = set()
    for term in terms:
        allow_tokens.update(re.findall(r"[a-z0-9]+", term.lower()))
    allow_tokens -= {"disease", "syndrome", "due", "to", "type", "ii", "the", "and"}

    actions = {"trials": None, "grants": None, "papers": None}
    if live:
        trials = search_clinicaltrials(search_label, page_size=8)
        actions["trials"] = filter_trials(trials, allow_tokens=allow_tokens)
        actions["grants"] = search_reporter(search_label, limit=5)
        actions["papers"] = search_pubmed(search_label, retmax=5)

    next_steps = _suggest_next_steps(resolved, similar, actions)

    return {
        "status": "ok",
        "query": query,
        "resolve": resolved,
        "similar_diseases": similar,
        "actions": actions,
        "next_steps": next_steps,
    }


def _suggest_next_steps(resolved: dict, similar: dict, actions: dict) -> list[dict]:
    steps: list[dict] = []
    primary = resolved.get("primary") or {}
    if primary.get("kind") == "gene":
        steps.append(
            {
                "action": "review_gene_linked_diseases",
                "detail": f"Review all subtypes and related entries for gene {primary.get('symbol')}.",
            }
        )
    neighbors = similar.get("neighbors") or []
    if neighbors:
        top = neighbors[0]
        steps.append(
            {
                "action": "compare_neighbor",
                "detail": f"Compare evidence with {top['name']} (confidence {top['confidence']}, warnings: {top['warnings'] or 'none'}).",
                "disease_key": top["disease_key"],
            }
        )
    trials = (actions.get("trials") or {}).get("records") or []
    if trials:
        steps.append(
            {
                "action": "open_trial",
                "detail": f"Review trial {trials[0]['nct_id']} and confirm eligibility before contacting sites.",
                "url": trials[0]["url"],
            }
        )
    grants = (actions.get("grants") or {}).get("records") or []
    if grants:
        steps.append(
            {
                "action": "contact_pi",
                "detail": f"Consider reaching out to {grants[0]['pi']} ({grants[0]['organization']}).",
                "url": grants[0]["url"],
            }
        )
    if not neighbors and not trials:
        steps.append(
            {
                "action": "fill_evidence_gap",
                "detail": "No strong neighbor or trial match in current atlas coverage; fetch phenotype/pathway sources and rebuild.",
            }
        )
    return steps
