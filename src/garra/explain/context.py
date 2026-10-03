"""Build a citation-indexed evidence packet from a journey dict."""

from __future__ import annotations

from garra.sources.envelope import now_iso


def build_evidence_packet(journey: dict) -> dict:
    """Flatten journey into cite-id keyed facts the model may reference."""
    citations: list[dict] = []
    facts: list[dict] = []

    def add(kind: str, label: str, *, source: str, url: str = "", detail: dict | None = None) -> str:
        cite_id = f"E{len(citations) + 1}"
        citations.append(
            {
                "id": cite_id,
                "kind": kind,
                "label": label,
                "source": source,
                "url": url,
                "detail": detail or {},
            }
        )
        facts.append({"cite_id": cite_id, "kind": kind, "label": label})
        return cite_id

    resolved = journey.get("resolve") or {}
    primary = resolved.get("primary") or {}
    if primary.get("kind") == "disease":
        add(
            "anchor_disease",
            f"{primary.get('name')} ({primary.get('disease_key')})",
            source="local_atlas",
            detail={"disease_key": primary.get("disease_key")},
        )
    elif primary.get("kind") == "gene":
        add(
            "anchor_gene",
            f"Gene {primary.get('symbol')} ({primary.get('gene_key')})",
            source="local_atlas",
            detail={"gene_key": primary.get("gene_key")},
        )

    similar = journey.get("similar_diseases") or {}
    anchor = similar.get("anchor") or {}
    if anchor.get("genes"):
        add(
            "anchor_genes",
            "Associated genes: " + ", ".join(anchor["genes"]),
            source="HPO / Orphanet / Monarch (ingested edges)",
        )

    coverage = similar.get("coverage") or {}
    if not coverage.get("phenotype_edges"):
        add(
            "coverage_gap",
            "Phenotype (HPO) overlap not loaded in this atlas build",
            source="ingest_status",
        )
    if not coverage.get("pathway_edges"):
        add(
            "coverage_gap",
            "Pathway overlap not loaded in this atlas build",
            source="ingest_status",
        )

    for neighbor in (similar.get("neighbors") or [])[:5]:
        warnings = neighbor.get("warnings") or []
        add(
            "neighbor",
            f"{neighbor.get('name')} — confidence {neighbor.get('confidence')}, "
            f"reasons {', '.join(neighbor.get('reasons') or [])}",
            source="garra.similarity (local graph)",
            detail={
                "disease_key": neighbor.get("disease_key"),
                "score": neighbor.get("score"),
                "warnings": warnings,
                "shared_genes": neighbor.get("shared_genes"),
                "shared_hpo_count": neighbor.get("shared_hpo_count"),
                "shared_pathway_count": neighbor.get("shared_pathway_count"),
            },
        )

    actions = journey.get("actions") or {}
    trials = (actions.get("trials") or {}).get("records") or []
    for trial in trials[:4]:
        add(
            "clinical_trial",
            f"{trial.get('nct_id')}: {trial.get('title')} ({trial.get('status')})",
            source="ClinicalTrials.gov",
            url=trial.get("url") or "",
            detail={"conditions": trial.get("conditions")},
        )
    for dropped in (actions.get("trials") or {}).get("filtered_out") or []:
        add(
            "trial_filtered",
            f"Excluded as TEXT_MISMATCH: {dropped.get('nct_id')} — {dropped.get('title')}",
            source="garra.actions.journey",
            detail={"conditions": dropped.get("conditions")},
        )

    grants = (actions.get("grants") or {}).get("records") or []
    for grant in grants[:3]:
        add(
            "nih_grant",
            f"{grant.get('project_num')}: {grant.get('title')} — PI {grant.get('pi')}",
            source="NIH RePORTER",
            url=grant.get("url") or "",
        )

    papers = (actions.get("papers") or {}).get("records") or []
    for paper in papers[:3]:
        add(
            "publication",
            f"PMID {paper.get('pmid')}: {paper.get('title')}",
            source="PubMed",
            url=paper.get("url") or "",
        )

    for step in journey.get("next_steps") or []:
        add("next_step", step.get("detail") or "", source="garra.actions.journey", url=step.get("url") or "")

    return {
        "query": journey.get("query"),
        "generated_at": now_iso(),
        "citations": citations,
        "facts": facts,
        "rules": [
            "Only cite using the provided citation ids (E1, E2, ...).",
            "Do not invent trials, grants, researchers, or URLs.",
            "State uncertainties when warnings or coverage gaps are present.",
            "Write for a patient group leader, not a physician.",
        ],
    }
