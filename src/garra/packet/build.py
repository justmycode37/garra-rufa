"""Assemble connections packet v1 from cached API responses (one anchor)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from garra.actions.journey import build_journey
from garra.connections import build_connections
from garra.explain.context import augment_evidence_with_connections, build_evidence_packet
from garra.explain.explain import explain_journey
from garra.explain.fallback import fallback_narrative
from garra.packet import cache as disk
from garra.packet.fetch import (
    monarch_disease_genes,
    monarch_disease_phenotypes,
    monarch_entity,
    monarch_semsim,
    opentargets_pathways_for_symbol,
    orpha_from_mondo_entity,
    orphadata_genes,
    orphadata_phenotypes,
    unavailable_message,
)
from garra.packet.resolve import resolve_anchor_query
from garra.sources.envelope import envelope, now_iso

MONARCH_DOCS = "https://api.monarchinitiative.org/v3/docs"
OT_URL = "https://platform.opentargets.org"
ORPHADATA_URL = "https://api.orphadata.com"


def _evidence_id(prefix: str, *parts: str) -> str:
    slug = "_".join(p.replace(":", "") for p in parts if p)
    return f"{prefix}_{slug}"[:64]


def _load_step(
    path: Path,
    *,
    use_cache: bool,
    fetcher,
    retrieved_at: str | None = None,
) -> tuple[Any, str]:
    if use_cache:
        ts, body = disk.read_cached(path)
        if body is not None and ts:
            return body, ts
    body = fetcher()
    ts = body.get("retrieved_at") if isinstance(body, dict) else retrieved_at or now_iso()
    disk.write_json(path, body, retrieved_at=ts)
    return body, ts


def build_connections_packet(
    query: str,
    *,
    use_cache: bool = True,
    candidate_limit: int = 5,
    semsim_limit: int = 20,
    max_genes_for_pathways: int = 4,
) -> dict:
    """Fetch (or read cache) Monarch + Orphadata + Open Targets for one anchor query."""
    if use_cache:
        complete = disk.try_load_complete_packet(
            query,
            candidate_limit=candidate_limit,
            semsim_limit=semsim_limit,
        )
        if complete is not None:
            return complete

    search, _network = resolve_anchor_query(query, use_cache=use_cache)
    item = search["item"]
    anchor_id = item["id"]
    anchor_name = item.get("name") or query
    base = disk.anchor_dir(anchor_id)

    search_path = base / "monarch_search.json"
    if use_cache:
        _ts, existing = disk.read_cached(search_path)
        if existing is None:
            disk.write_json(search_path, search, retrieved_at=search["retrieved_at"])
    else:
        disk.write_json(search_path, search, retrieved_at=search["retrieved_at"])

    entity_body, entity_ts = _load_step(
        base / "monarch_entity.json",
        use_cache=use_cache,
        fetcher=lambda: monarch_entity(anchor_id),
    )
    entity = entity_body["entity"]
    equivalent_ids = [
        x
        for x in (entity.get("xref") or [])
        if isinstance(x, str) and x != anchor_id and not x.startswith("UMLS:")
    ][:12]

    pheno_body, pheno_ts = _load_step(
        base / "monarch_anchor_phenotypes.json",
        use_cache=use_cache,
        fetcher=lambda: monarch_disease_phenotypes(anchor_id),
    )
    anchor_hps = set(pheno_body["hpo_ids"])

    genes_body, genes_ts = _load_step(
        base / "monarch_anchor_genes.json",
        use_cache=use_cache,
        fetcher=lambda: monarch_disease_genes(anchor_id),
    )
    anchor_genes = genes_body["gene_symbols"]

    orpha = orpha_from_mondo_entity(entity)
    orpha_pheno_ts = orpha_gene_ts = None
    if orpha:
        try:
            op_body, orpha_pheno_ts = _load_step(
                base / "orphadata_phenotypes.json",
                use_cache=use_cache,
                fetcher=lambda: orphadata_phenotypes(orpha),
            )
            anchor_hps |= set(op_body["hpo_ids"])
        except Exception:
            pass
        try:
            og_body, orpha_gene_ts = _load_step(
                base / "orphadata_genes.json",
                use_cache=use_cache,
                fetcher=lambda: orphadata_genes(orpha),
            )
            for g in og_body["gene_symbols"]:
                if g not in anchor_genes:
                    anchor_genes.append(g)
        except Exception:
            pass

    hpo_fingerprint = hashlib.sha256(",".join(sorted(anchor_hps)).encode()).hexdigest()[:12]
    semsim_path = base / f"monarch_semsim_{hpo_fingerprint}.json"
    if use_cache:
        ts, cached = disk.read_cached(semsim_path)
        if cached is not None and ts:
            semsim_body = cached
            semsim_ts = ts
        else:
            semsim_body = monarch_semsim(sorted(anchor_hps), limit=semsim_limit)
            semsim_ts = semsim_body["retrieved_at"]
            disk.write_json(semsim_path, semsim_body, retrieved_at=semsim_ts)
    else:
        semsim_body = monarch_semsim(sorted(anchor_hps), limit=semsim_limit)
        semsim_ts = semsim_body["retrieved_at"]
        disk.write_json(semsim_path, semsim_body, retrieved_at=semsim_ts)

    semsim_results = semsim_body.get("results") or []
    metric = semsim_body.get("metric") or "jaccard_similarity"
    release = semsim_body.get("release") or "unknown"

    evidence: dict[str, dict] = {}
    papers: list[dict] = []
    candidates: list[dict] = []
    excluded = {anchor_id, *equivalent_ids}

    def add_evidence(
        eid: str,
        *,
        source: str,
        url: str,
        retrieved_at: str,
        locator: str,
        passage: str = "",
    ) -> str:
        if eid not in evidence:
            evidence[eid] = {
                "id": eid,
                "source": source,
                "url": url,
                "retrieved_at": retrieved_at,
                "locator": locator,
                "paper_ids": [],
            }
            if passage:
                evidence[eid]["passage"] = passage[:500]
        return eid

    if orpha and orpha_pheno_ts:
        add_evidence(
            _evidence_id("EV", "anchor", "orphadata"),
            source="Orphadata",
            url=ORPHADATA_URL,
            retrieved_at=orpha_pheno_ts,
            locator=f"{orpha} phenotypes",
            passage=f"Anchor profile includes {len(anchor_hps)} HPO terms (Monarch + Orphadata).",
        )

    anchor_pathways: dict[str, set[str]] = {}
    for sym in anchor_genes[:max_genes_for_pathways]:
        try:
            pt, _ = _load_step(
                base / "opentargets" / f"{sym}.json",
                use_cache=use_cache,
                fetcher=lambda s=sym: opentargets_pathways_for_symbol(s),
            )
            anchor_pathways[sym] = set(pt.get("pathway_ids") or [])
        except Exception:
            anchor_pathways[sym] = set()

    picked = 0
    for row in semsim_results:
        if picked >= candidate_limit:
            break
        cid = row["id"]
        if cid in excluded:
            continue
        cname = row["name"]
        score = row["value"]

        cand_dir = base / "candidates" / cid.replace(":", "_")
        try:
            cp_body, cp_ts = _load_step(
                cand_dir / "monarch_phenotypes.json",
                use_cache=use_cache,
                fetcher=lambda i=cid: monarch_disease_phenotypes(i),
            )
            cand_hps = set(cp_body["hpo_ids"])
        except Exception:
            cand_hps = set()

        try:
            cg_body, cg_ts = _load_step(
                cand_dir / "monarch_genes.json",
                use_cache=use_cache,
                fetcher=lambda i=cid: monarch_disease_genes(i),
            )
            cand_genes = cg_body["gene_symbols"]
        except Exception:
            cand_genes = []

        shared_hps = sorted(anchor_hps & cand_hps)
        shared_genes = sorted(set(anchor_genes) & set(cand_genes))

        cand_pathways: set[str] = set()
        pathway_labels: dict[str, str] = {}
        for sym in cand_genes[:max_genes_for_pathways]:
            try:
                pt, _ = _load_step(
                    cand_dir / "opentargets" / f"{sym}.json",
                    use_cache=use_cache,
                    fetcher=lambda s=sym: opentargets_pathways_for_symbol(s),
                )
                for p in pt.get("pathways") or []:
                    pid = p.get("pathwayId")
                    if pid:
                        cand_pathways.add(pid)
                        pathway_labels[pid] = p.get("pathway") or pid
            except Exception:
                continue

        shared_pathways: set[str] = set()
        for paths in anchor_pathways.values():
            shared_pathways |= paths & cand_pathways

        claims: list[dict] = []
        ev_sim = add_evidence(
            _evidence_id("EV", cid, "semsim"),
            source="Monarch Initiative",
            url=MONARCH_DOCS,
            retrieved_at=semsim_ts,
            locator=f"semsim/search subject={cid}",
            passage=f"{metric}={score}; matched phenotypes={len(row.get('matched_phenotypes') or [])}.",
        )

        if shared_hps:
            ev_hp = add_evidence(
                _evidence_id("EV", cid, "hp"),
                source="Monarch Initiative associations",
                url=f"https://api-v3.monarchinitiative.org/v3/api/association?subject={anchor_id}",
                retrieved_at=pheno_ts,
                locator="HPO term intersection (anchor vs candidate)",
                passage="Shared HPO: " + ", ".join(shared_hps[:8])
                + ("…" if len(shared_hps) > 8 else ""),
            )
            claims.append(
                {
                    "id": "C_phenotype",
                    "relationship": "shared_phenotype",
                    "statement": f"{len(shared_hps)} shared HPO phenotype term(s) between diseases.",
                    "assessment": "supported",
                    "reviewed": False,
                    "supporting_evidence_ids": [ev_hp, ev_sim],
                    "contradicting_evidence_ids": [],
                    "context": "Derived from Monarch disease–phenotype associations.",
                    "limitations": "Phenotype overlap does not prove shared mechanism or treatment response.",
                }
            )

        if shared_pathways:
            sample = next(iter(shared_pathways))
            ev_pw = add_evidence(
                _evidence_id("EV", cid, "pathway"),
                source="Open Targets Platform",
                url=OT_URL,
                retrieved_at=now_iso(),
                locator=f"pathwayId={sample}",
                passage="Shared Reactome pathway IDs: "
                + ", ".join(sorted(shared_pathways)[:5]),
            )
            claims.append(
                {
                    "id": "C_pathway",
                    "relationship": "shared_pathway",
                    "statement": "At least one shared pathway annotation links causal genes in both diseases.",
                    "assessment": "supported",
                    "reviewed": False,
                    "supporting_evidence_ids": [ev_pw],
                    "contradicting_evidence_ids": [],
                    "context": pathway_labels.get(sample, sample),
                    "limitations": "Pathway co-membership is not proof of identical molecular effect.",
                }
            )

        if shared_genes:
            ev_gene = add_evidence(
                _evidence_id("EV", cid, "gene"),
                source="Monarch Initiative / Orphadata gene associations",
                url=MONARCH_DOCS,
                retrieved_at=genes_ts,
                locator="Gene symbol intersection",
                passage="Shared genes: " + ", ".join(shared_genes),
            )
            claims.append(
                {
                    "id": "C_gene",
                    "relationship": "shared_gene",
                    "statement": f"Shared causal gene association(s): {', '.join(shared_genes)}.",
                    "assessment": "hypothesis",
                    "reviewed": False,
                    "supporting_evidence_ids": [ev_gene],
                    "contradicting_evidence_ids": [],
                    "context": "Gene overlap alone does not establish a supported biological connection.",
                    "limitations": "Variants and molecular effects may differ.",
                }
            )

        coverage = {
            "phenotype": "available" if cand_hps else "missing",
            "pathway": "available" if shared_pathways or anchor_pathways else "unknown",
            "molecular_effect": "unknown",
        }

        candidates.append(
            {
                "id": cid,
                "name": cname,
                "similarity": {
                    "provider": "Monarch Initiative",
                    "metric": metric,
                    "value": float(score),
                    "release": release,
                    "retrieved_at": semsim_ts,
                    "source_url": MONARCH_DOCS,
                },
                "coverage": coverage,
                "claims": claims,
                "assets": [],
            }
        )
        picked += 1

    packet = {
        "schema_version": 1,
        "anchor": {
            "id": anchor_id,
            "name": anchor_name,
            "equivalent_ids": equivalent_ids,
        },
        "papers": papers,
        "evidence": list(evidence.values()),
        "candidates": candidates,
        "fetch_meta": {
            "build_version": disk.PACKET_BUILD_VERSION,
            "query": query,
            "cache_dir": str(base),
            "built_at": now_iso(),
            "anchor_hpo_count": len(anchor_hps),
            "orpha": orpha,
            "candidate_limit": candidate_limit,
            "semsim_limit": semsim_limit,
            "max_genes_for_pathways": max_genes_for_pathways,
        },
    }

    disk.register_query(
        query,
        anchor_id=anchor_id,
        anchor_name=anchor_name,
        extra_keys=[anchor_id, anchor_name],
    )

    packet_path = base / "packet.json"
    packet_path.write_text(
        json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    manifest = {
        "anchor_id": anchor_id,
        "query": query,
        "retrieved_at": now_iso(),
        "steps": {
            "monarch_search": search["retrieved_at"],
            "monarch_entity": entity_ts,
            "monarch_phenotypes": pheno_ts,
            "monarch_genes": genes_ts,
            "monarch_semsim": semsim_ts,
            "orphadata_phenotypes": orpha_pheno_ts,
            "orphadata_genes": orpha_gene_ts,
        },
        "packet": str(packet_path),
    }
    disk.write_json(base / "manifest.json", manifest, retrieved_at=manifest["retrieved_at"])
    return packet


def run_pipeline(
    query: str,
    *,
    use_cache: bool = True,
    candidate_limit: int = 5,
    min_score: float = 50,
    live: bool = True,
    use_openai: bool = False,
) -> dict:
    """One anchor: fetch packet → build_connections → explain_query (no 7200-disease loop)."""
    errors: list[str] = []
    try:
        packet = build_connections_packet(
            query, use_cache=use_cache, candidate_limit=candidate_limit
        )
    except Exception as exc:
        return {
            "status": "error",
            "query": query,
            "message": unavailable_message(exc),
            "errors": [str(exc)],
        }

    try:
        connections = build_connections(packet, min_score=min_score, limit=candidate_limit)
    except ValueError as exc:
        errors.append(f"build_connections: {exc}")
        connections = {"status": "error", "message": str(exc)}

    journey = build_journey(query, neighbor_limit=candidate_limit, live=live)
    conn_ok = connections.get("status") in ("ok", "no_matches")
    if journey.get("status") == "ok":
        explain_block = explain_journey(
            journey,
            use_openai=use_openai,
            connections=connections if conn_ok else None,
        )
    elif conn_ok:
        evidence_packet = build_evidence_packet(journey)
        augment_evidence_with_connections(evidence_packet, connections)
        narrative = fallback_narrative(evidence_packet)
        explain_block = {
            "status": "ok",
            "source_id": "garra.explain",
            "publisher": "garra-rufa (deterministic fallback)",
            "license": "n/a",
            "url": "local",
            "retrieved_at": now_iso(),
            "query": query,
            "narrative": narrative,
            "evidence": evidence_packet,
            "journey_status": journey.get("status"),
            "journey_message": journey.get("message"),
        }
    else:
        explain_block = envelope(
            status=journey.get("status", "rejected"),
            source_id="garra.explain",
            publisher="garra-rufa",
            license="n/a",
            url="local",
            message=journey.get("message") or "Journey and connections both failed.",
        )

    explanation = {
        "status": explain_block.get("status", "ok"),
        "query": query,
        "journey": journey,
        "explanation": explain_block,
    }

    cache_dir = (packet.get("fetch_meta") or {}).get("cache_dir")
    pipeline_ok = conn_ok and explain_block.get("status") == "ok"
    return {
        "status": "ok" if pipeline_ok else "error",
        "query": query,
        "cache_dir": cache_dir,
        "packet": packet,
        "connections": connections,
        "explanation": explanation,
        "errors": errors,
    }
