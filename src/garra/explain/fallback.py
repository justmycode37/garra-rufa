"""Deterministic explanation when OpenAI is unavailable."""

from __future__ import annotations


def fallback_narrative(packet: dict) -> dict:
    citations = packet.get("citations") or []
    by_kind = {}
    for c in citations:
        by_kind.setdefault(c["kind"], []).append(c)

    lines: list[str] = []
    cite_ids: list[str] = []

    anchors = by_kind.get("anchor_disease") or by_kind.get("anchor_gene") or []
    if anchors:
        lines.append(f"We matched your search to {anchors[0]['label']}.")
        cite_ids.append(anchors[0]["id"])

    genes = by_kind.get("anchor_genes")
    if genes:
        lines.append(genes[0]["label"] + ".")
        cite_ids.append(genes[0]["id"])

    neighbors = by_kind.get("neighbor") or []
    if neighbors:
        n = neighbors[0]
        lines.append(
            f"The closest related entry in our atlas is {n['label'].split(' — ')[0]}. "
            "Review the shared evidence before assuming the same treatment path applies."
        )
        cite_ids.append(n["id"])
        warnings = (n.get("detail") or {}).get("warnings") or []
        if warnings:
            lines.append(f"Flags on that link: {', '.join(warnings)}.")

    gaps = by_kind.get("coverage_gap") or []
    uncertainties = [g["label"] for g in gaps]

    trials = by_kind.get("clinical_trial") or []
    if trials:
        t = trials[0]
        lines.append(f"One registered study to review: {t['label'].split(': ', 1)[-1]}.")
        cite_ids.append(t["id"])

    grants = by_kind.get("nih_grant") or []
    if grants:
        g = grants[0]
        lines.append(f"Active NIH-funded work includes {g['label'].split(': ', 1)[-1]}.")
        cite_ids.append(g["id"])

    filtered = by_kind.get("trial_filtered") or []
    if filtered:
        uncertainties.append(
            f"{len(filtered)} trial(s) were hidden because conditions did not match your disease."
        )

    next_steps = by_kind.get("next_step") or []
    next_step = next_steps[0]["label"] if next_steps else "Rebuild the atlas after phenotype and pathway files are fetched."

    if not lines:
        lines.append("We could not assemble a supported path from the current atlas coverage.")

    return {
        "mode": "fallback",
        "title": f"Atlas path for {packet.get('query') or 'your search'}",
        "summary_for_family": " ".join(lines),
        "sections": [
            {
                "heading": "What we found",
                "body": " ".join(lines),
                "citations": list(dict.fromkeys(cite_ids)),
            }
        ],
        "uncertainties": uncertainties,
        "next_step_this_week": next_step,
    }
