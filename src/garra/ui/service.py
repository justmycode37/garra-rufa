"""Application boundary: confirmed HPO IDs -> score cards + sourced enrichment."""

from copy import deepcopy

from garra.connections import build_connections
from garra.connections.builder import _index, _number, _object

from .catalog import IDS, SYMPTOMS
from .monarch import METRICS, MonarchClient


class InputError(ValueError):
    pass


def validate_request(body):
    if not isinstance(body, dict):
        raise InputError("Request must be a JSON object")
    allowed = {"hpo_ids", "confirmed", "metric", "min_score", "limit"}
    if set(body) - allowed:
        raise InputError(
            "Unknown fields. Send confirmed HPO IDs, not body coordinates or patient details."
        )
    if body.get("confirmed") is not True:
        raise InputError("Confirm individual symptoms before searching")
    ids = body.get("hpo_ids")
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 20
        or any(not isinstance(i, str) or i not in IDS for i in ids)
    ):
        raise InputError(
            "Select 1–20 HPO IDs from GET /api/body-map; this starter menu is intentionally limited"
        )
    metric = body.get("metric", "jaccard_similarity")
    if not isinstance(metric, str) or metric not in METRICS:
        raise InputError("Unsupported similarity metric")
    minimum = body.get("min_score", 50 if metric == "jaccard_similarity" else None)
    if metric != "jaccard_similarity" and minimum is not None:
        raise InputError(
            "min_score only applies to jaccard_similarity; use null for native metrics"
        )
    if metric == "jaccard_similarity":
        try:
            minimum = _number(minimum, "min_score")
            if not 0 <= minimum <= 100:
                raise ValueError("out of range")
        except ValueError as exc:
            raise InputError("min_score must be a finite number from 0 to 100") from exc
    limit = body.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
        raise InputError("limit must be an integer from 1 to 25")
    return sorted(set(ids)), metric, minimum, limit


class ConnectionService:
    def __init__(self, client=None, enrichment=None):
        self.client = client or MonarchClient()
        self.enrichment = deepcopy(
            enrichment
            if enrichment is not None
            else {"schema_version": 1, "diseases": [], "evidence": [], "papers": [], "mappings": {}}
        )
        e = _object(self.enrichment, "enrichment")
        if (
            type(e.get("schema_version")) is not int
            or e.get("schema_version") != 1
            or e.get("demo")
        ):
            raise ValueError("Enrichment must be a real version-1 disease research catalog")
        self.diseases = {}
        for row in _index(e.get("diseases", []), "diseases").values():
            if row["id"] in self.diseases or row.get("scope") != "disease_research":
                raise ValueError(
                    "Disease records need unique IDs and scope=disease_research; pairwise claims cannot be transferred to a symptom query"
                )
            self.diseases[row["id"]] = row
        self.mappings = e.get("mappings", {})
        if not isinstance(self.mappings, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) or v not in self.diseases
            for k, v in self.mappings.items()
        ):
            raise ValueError("Every reviewed mapping must point to a catalog disease")
        # Validate all evidence and claims at startup, including records not yet retrieved.
        candidates = []
        for row in self.diseases.values():
            candidates.append(
                {
                    **row,
                    "similarity": {
                        "provider": "catalog-validation",
                        "metric": "jaccard_similarity",
                        "value": 0,
                        "release": "validation",
                        "retrieved_at": "validation",
                        "source_url": "https://example.org/validation",
                    },
                }
            )
        build_connections(
            {
                "schema_version": 1,
                "anchor": {"id": "PROFILE:validation", "name": "Validation"},
                "candidates": candidates,
                "evidence": e.get("evidence", []),
                "papers": e.get("papers", []),
            },
            min_score=0,
        )

    def search(self, body):
        ids, metric, minimum, limit = validate_request(body)
        fetched = self.client.search(ids, metric, limit=50)
        candidates = []
        metadata = {}
        for match in fetched["results"]:
            canonical = self.mappings.get(match["id"], match["id"])
            research = self.diseases.get(canonical, {})
            candidates.append(
                {
                    "id": match["id"],
                    "name": match["name"],
                    "similarity": {
                        "provider": "Monarch Initiative",
                        "metric": metric,
                        "value": match["value"],
                        "release": fetched["release"],
                        "retrieved_at": fetched["retrieved_at"],
                        "source_url": fetched["source_url"],
                    },
                    "coverage": research.get(
                        "coverage",
                        {
                            "phenotype": "available",
                            "pathway": "unknown",
                            "molecular_effect": "unknown",
                        },
                    ),
                    "claims": research.get("claims", []),
                    "assets": research.get("assets", []),
                }
            )
            metadata[match["id"]] = {**match, "enrichment_id": canonical if research else None}
        packet = {
            "schema_version": 1,
            "anchor": {"id": "PROFILE:selected-symptoms", "name": "Your selected symptoms"},
            "candidates": candidates,
            "evidence": self.enrichment.get("evidence", []),
            "papers": self.enrichment.get("papers", []),
        }
        result = build_connections(packet, min_score=minimum or 0, limit=limit)
        for card in result["cards"]:
            meta = metadata[card["id"]]
            card["matched_phenotypes"] = meta["matched_phenotypes"]
            card["annotation_counts"] = {
                "disease": meta["disease_annotation_count"],
                "query": meta["query_term_count"],
            }
            card["enrichment_id"] = meta["enrichment_id"]
            card["evidence_scope"] = "disease_research_not_patient_mechanism"
            card["similarity"]["label"] = "Similarity to your selected symptoms"
            card["similarity"]["interpretation"] = (
                "Ontology-based phenotype similarity, not percent symptoms matched, diagnosis probability, or treatment response."
            )
            card["why_shown"] = (
                "Monarch matched this disease profile to your confirmed symptoms. "
                + (
                    "The score passes your discovery filter."
                    if minimum is not None
                    else "Ranked in the selected metric’s native units."
                )
            )
            card["warnings"].append(
                "Human Diseases includes conditions beyond rare genetic diseases."
            )
            if meta["disease_annotation_count"] < 3:
                card["warnings"].append(
                    "Sparse disease annotations: a high similarity score may be based on fewer than three recorded phenotypes."
                )
            if not meta["enrichment_id"]:
                card["warnings"].append(
                    "No verified biological claims, assets, or papers have been supplied for this disease."
                )
        result["min_score"] = minimum
        result["query"] = {
            "mode": "symptom_profile",
            "hpo_ids": ids,
            "symptoms": deepcopy([s for s in SYMPTOMS if s["id"] in ids]),
            "metric": metric,
            "min_score": minimum,
            "limit": limit,
        }
        result["upstream"] = {
            k: fetched[k] for k in ("source_url", "retrieved_at", "release", "cache")
        }
        result["retrieval"] = {
            "candidate_limit": 50,
            "received": len(fetched["results"]),
            "scope": "Top 50 upstream matches, not an exhaustive disease search",
        }
        result["notice"] = (
            "Research exploration, not diagnosis. Body location is not used in the similarity calculation. Enrichment describes disease research, not proof of a mechanism in the user."
        )
        return result
