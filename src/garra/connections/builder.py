"""Validate the fetch boundary and assemble transparent, evidence-linked cards."""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from urllib.parse import urlsplit

RELATIONS = {"shared_gene", "shared_pathway", "shared_phenotype", "shared_molecular_effect"}


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _url(value, label):
    _text(value, label)
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError(f"{label} must be an HTTP(S) URL without credentials")
    return value


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        finite = isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _index(rows, label):
    if not isinstance(rows, list):
        raise ValueError(f"{label} must be a list")
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{label} entries must be objects")
        key = _text(row.get("id"), f"{label}.id")
        if key in out:
            raise ValueError(f"duplicate {label} id: {key}")
        out[key] = row
    return out


def _refs(row, key, index, *, required=False):
    refs = row.get(key, [])
    if not isinstance(refs, list) or any(not isinstance(r, str) or r not in index for r in refs):
        raise ValueError(f"{row.get('id')}: invalid {key}")
    if required and not refs:
        raise ValueError(f"{row.get('id')}: {key} cannot be empty")
    return refs


def build_connections(packet: dict, *, min_score: float = 50, limit: int = 5) -> dict:
    """Build cards from contract v1; min_score applies only to Jaccard scores.

    A Jaccard index is displayed as an index out of 100, never as treatment
    probability. Other metrics retain native units and bypass this filter,
    explicitly labeled. One response must use one provider/metric/release.
    """
    if (
        not isinstance(packet, dict)
        or type(packet.get("schema_version")) is not int
        or packet.get("schema_version") != 1
    ):
        raise ValueError("Expected connection packet schema_version=1")
    min_score = _number(min_score, "min_score")
    if not 0 <= min_score <= 100:
        raise ValueError("min_score must be between 0 and 100")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
        raise ValueError("limit must be an integer between 1 and 25")
    p = deepcopy(packet)
    anchor = _object(p.get("anchor", {}), "anchor")
    _text(anchor.get("id"), "anchor.id")
    _text(anchor.get("name"), "anchor.name")
    equivalent_ids = anchor.get("equivalent_ids", [])
    if not isinstance(equivalent_ids, list) or any(not isinstance(i, str) for i in equivalent_ids):
        raise ValueError("anchor.equivalent_ids must be a list of reviewed equivalent IDs")
    excluded = {anchor["id"], *equivalent_ids}
    papers = _index(p.get("papers", []), "papers")
    evidence = _index(p.get("evidence", []), "evidence")
    for paper in papers.values():
        _text(paper.get("title"), "paper.title")
        _url(paper.get("url"), "paper.url")
        if paper.get("access") not in ("open_access", "restricted", "unknown"):
            raise ValueError("paper.access must be open_access, restricted, or unknown")
        if paper.get("full_text_url"):
            _url(paper["full_text_url"], "paper.full_text_url")
            if paper["access"] != "open_access":
                raise ValueError("full_text_url requires verified open_access status")
    for ev in evidence.values():
        _text(ev.get("source"), "evidence.source")
        _url(ev.get("url"), "evidence.url")
        _text(ev.get("retrieved_at"), "evidence.retrieved_at")
        _text(ev.get("locator"), "evidence.locator")
        _refs(ev, "paper_ids", papers)
    candidates = _index(p.get("candidates", []), "candidates")
    cards, omitted = [], []
    signatures = set()
    for disease in candidates.values():
        _text(disease.get("name"), "candidate.name")
        score = _object(disease.get("similarity", {}), "similarity")
        raw = _number(score.get("value"), "similarity.value")
        for field in ("provider", "metric", "release", "retrieved_at"):
            _text(score.get(field), f"similarity.{field}")
        _url(score.get("source_url"), "similarity.source_url")
        # Only this documented bounded index has a built-in display conversion.
        bounded = score["metric"] == "jaccard_similarity"
        if raw < 0 or (bounded and raw > 1):
            raise ValueError("Similarity score is outside the metric's supported range")
        signatures.add((score["provider"], score["metric"], score["release"]))
        score["display_score"] = round(raw * 100, 1) if bounded else None
        score["display_label"] = f"{raw * 100:.1f}/100" if bounded else f"{raw:g} (native units)"
        score["threshold_applied"] = bounded
        score["interpretation"] = (
            "Phenotype similarity, not mechanism certainty or treatment probability."
        )
        claims = _index(disease.get("claims", []), "claims")
        for claim in claims.values():
            _text(claim.get("statement"), "claim.statement")
            if (
                not isinstance(claim.get("relationship"), str)
                or claim["relationship"] not in RELATIONS
            ):
                raise ValueError("Unknown claim relationship")
            if claim.get("assessment") not in ("supported", "hypothesis", "contradicted"):
                raise ValueError("Unknown claim assessment")
            _refs(claim, "supporting_evidence_ids", evidence)
            _refs(claim, "contradicting_evidence_ids", evidence)
            if claim["assessment"] == "supported" and not claim.get("supporting_evidence_ids"):
                raise ValueError("Supported claims need supporting evidence")
            if claim["assessment"] == "contradicted" and not claim.get(
                "contradicting_evidence_ids"
            ):
                raise ValueError("Contradicted claims need contradicting evidence")
            if claim.get("reviewed") is not None and type(claim["reviewed"]) is not bool:
                raise ValueError("claim.reviewed must be boolean")
            # Both sides remain visible. Conflicts cannot be silently promoted.
            claim["status"] = (
                "conflicting_evidence"
                if claim.get("contradicting_evidence_ids")
                else "supported"
                if claim["assessment"] == "supported" and claim.get("reviewed") is True
                else "hypothesis"
            )
        assets = _index(disease.get("assets", []), "assets")
        if claims.keys() & assets.keys():
            raise ValueError("Claim and asset IDs must be distinct within a disease")
        for asset in assets.values():
            for field in ("name", "kind", "owner", "access_conditions"):
                _text(asset.get(field), f"asset.{field}")
            _url(asset.get("url"), "asset.url")
            _refs(asset, "evidence_ids", evidence, required=True)
            _refs(asset, "claim_ids", claims, required=True)
            checks = asset.get("reuse_checks")
            if (
                not isinstance(checks, list)
                or not checks
                or any(not isinstance(c, str) or not c.strip() for c in checks)
            ):
                raise ValueError("Assets require explicit reuse_checks")
            asset["status"] = "potentially_reusable_requires_review"
        if disease["id"] in excluded:
            omitted.append({"id": disease["id"], "reason": "same_disease_or_reviewed_equivalent"})
            continue
        if bounded and raw * 100 < min_score:
            omitted.append({"id": disease["id"], "reason": "below_similarity_threshold"})
            continue
        linked_evidence = set()
        paper_claims = {}
        for claim in claims.values():
            refs = claim.get("supporting_evidence_ids", []) + claim.get(
                "contradicting_evidence_ids", []
            )
            linked_evidence.update(refs)
            for ref in refs:
                for paper_id in evidence[ref].get("paper_ids", []):
                    paper_claims.setdefault(paper_id, set()).add(claim["id"])
        for asset in assets.values():
            linked_evidence.update(asset["evidence_ids"])
            for ref in asset["evidence_ids"]:
                for paper_id in evidence[ref].get("paper_ids", []):
                    paper_claims.setdefault(paper_id, set()).add(asset["id"])
        supported = [
            c
            for c in claims.values()
            if c["status"] == "supported" and c["relationship"] != "shared_gene"
        ]
        mechanism = [c for c in supported if c["relationship"] == "shared_molecular_effect"]
        warnings = []
        if not bounded:
            warnings.append(
                "Native metric: the 0–100 threshold does not apply; ranked by native score."
            )
        if not mechanism:
            warnings.append(
                "Compatible molecular effect is not established by the reviewed claims."
            )
        if any(c["status"] == "conflicting_evidence" for c in claims.values()):
            warnings.append("Contradictory evidence requires review.")
        coverage = disease.get("coverage", {})
        if not isinstance(coverage, dict) or any(
            v not in ("available", "missing", "unknown") for v in coverage.values()
        ):
            raise ValueError("coverage values must be available, missing, or unknown")
        cards.append(
            {
                "id": disease["id"],
                "name": disease["name"],
                "similarity": score,
                "why_shown": f"Ranked by {score['provider']} {score['metric']}; "
                + (
                    f"meets the {min_score:g}/100 discovery filter."
                    if bounded
                    else "native score is not a percentage."
                ),
                "biological_status": "supported_relationship" if supported else "hypothesis_only",
                "coverage": coverage,
                "claims": list(claims.values()),
                "assets": list(assets.values()),
                "papers": [
                    {**papers[key], "linked_to": sorted(ids)}
                    for key, ids in sorted(paper_claims.items())
                ],
                "evidence": [evidence[key] for key in sorted(linked_evidence)],
                "warnings": warnings,
            }
        )
    if len(signatures) > 1:
        raise ValueError(
            "Candidates must use one provider, metric, and release for comparable ranking"
        )
    cards.sort(key=lambda c: (-c["similarity"]["value"], c["id"]))
    for card in cards[limit:]:
        omitted.append({"id": card["id"], "reason": "outside_result_limit"})
    return {
        "schema_version": 1,
        "status": "ok" if cards else "no_matches",
        "anchor": anchor,
        "demo": p.get("demo") is True,
        "min_score": min_score,
        "cards": cards[:limit],
        "omitted": omitted,
        "message": ""
        if cards
        else "No candidates meet the current filter. This does not establish that no biological connection exists.",
    }
