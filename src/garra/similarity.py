"""Score disease neighbors by shared genes, pathways, and phenotypes."""

from __future__ import annotations

from garra.graph.store import AtlasStore
from garra.paths import ATLAS_DB


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _confidence(score: float, *, has_pathway: bool, has_phenotype: bool) -> str:
    if has_pathway and score >= 0.15:
        return "high"
    if has_phenotype and score >= 0.2:
        return "medium"
    if score >= 0.25:
        return "medium"
    return "low"


def similar_diseases(
    *,
    disease_key: str | None = None,
    gene_key: str | None = None,
    limit: int = 8,
    db_path=None,
) -> dict:
    store = AtlasStore(db_path or ATLAS_DB)
    if not disease_key and not gene_key:
        return {"status": "rejected", "message": "Provide disease_key or gene_key", "neighbors": []}

    if gene_key and not disease_key:
        anchor_diseases = store.diseases_for_gene(gene_key)
        if not anchor_diseases:
            return {
                "status": "not_found",
                "message": f"No diseases linked to gene {gene_key}",
                "neighbors": [],
            }
        disease_key = anchor_diseases[0]["disease_key"]

    assert disease_key is not None
    genes_a = {row["gene_key"]: row["symbol"] for row in store.genes_for_disease(disease_key)}
    pheno_a = store.phenotypes_for_disease(disease_key)
    path_a = store.pathways_for_disease(disease_key)

    with store.connect() as conn:
        all_diseases = conn.execute(
            "SELECT disease_key, primary_name FROM disease WHERE disease_key NOT LIKE 'GENE:%'"
        ).fetchall()

    neighbors: list[dict] = []
    for row in all_diseases:
        other_key = row["disease_key"]
        if other_key == disease_key:
            continue
        genes_b = {g["gene_key"]: g["symbol"] for g in store.genes_for_disease(other_key)}
        shared_genes = sorted(set(genes_a) & set(genes_b))
        pheno_b = store.phenotypes_for_disease(other_key)
        path_b = store.pathways_for_disease(other_key)
        shared_hpo = sorted(pheno_a & pheno_b)
        shared_pathways = sorted(path_a & path_b)

        gene_score = len(shared_genes) / max(len(set(genes_a) | set(genes_b)), 1)
        pheno_score = _jaccard(pheno_a, pheno_b)
        path_score = _jaccard(path_a, path_b)

        score = 0.45 * path_score + 0.35 * pheno_score + 0.20 * gene_score
        if not shared_genes and not shared_hpo and not shared_pathways:
            continue

        warnings: list[str] = []
        reasons: list[str] = []
        if shared_pathways:
            reasons.append("shared_pathway")
        if shared_hpo:
            reasons.append("shared_phenotype")
        if shared_genes:
            reasons.append("shared_gene")

        if shared_genes and not shared_pathways and pheno_score < 0.15:
            warnings.append("SAME_GENE_SUBTYPE")
        if pheno_score >= 0.2 and path_score < 0.05 and not shared_genes:
            warnings.append("MECHANISM_UNPROVEN")
        if pheno_score > 0 and path_score == 0 and not shared_pathways:
            warnings.append("PHENOTYPE_ONLY")

        assertion = "curated" if (shared_genes or shared_hpo) else "inferred"
        if path_score > 0 and not shared_hpo:
            assertion = "inferred"

        neighbors.append(
            {
                "disease_key": other_key,
                "name": row["primary_name"],
                "score": round(score, 4),
                "confidence": _confidence(score, has_pathway=bool(shared_pathways), has_phenotype=bool(shared_hpo)),
                "assertion": assertion,
                "reasons": reasons,
                "shared_genes": [genes_a.get(g, genes_b.get(g, g)) for g in shared_genes],
                "shared_hpo_count": len(shared_hpo),
                "shared_hpo_sample": shared_hpo[:8],
                "shared_pathway_count": len(shared_pathways),
                "shared_pathway_sample": shared_pathways[:5],
                "warnings": warnings,
                "support": {
                    "gene_jaccard": round(gene_score, 4),
                    "phenotype_jaccard": round(pheno_score, 4),
                    "pathway_jaccard": round(path_score, 4),
                },
            }
        )

    neighbors.sort(key=lambda item: item["score"], reverse=True)
    neighbors = neighbors[: max(1, min(limit, 25))]

    anchor_name = next((d["primary_name"] for d in all_diseases if d["disease_key"] == disease_key), disease_key)
    return {
        "status": "ok" if neighbors else "not_found",
        "anchor": {"disease_key": disease_key, "name": anchor_name, "genes": list(genes_a.values())},
        "neighbors": neighbors,
        "coverage": {
            "phenotype_edges": bool(pheno_a),
            "pathway_edges": bool(path_a),
        },
        "message": "" if neighbors else "No neighbors with shared gene, HPO, or pathway evidence.",
    }
