"""Pass 2: read one paper (full text when available) and extract evidence-backed edges.

The LLM gets the disease profile, the paper as numbered passages ([P12 | Results] ...)
and a controlled vocabulary, and returns

  entities  {key, name, type, synonyms}
  edges     {subject, predicate, object (entity keys), effect, evidence_level, organism,
             evidence: [{passage: "P12", quote: "<verbatim sentence>"}]}

Every edge needs at least one verbatim quote; verify.py checks them against the text and
drops edges without a verified one, so an edge in the final graph always points at the
sentence that states it.
"""
import re

ENTITY_TYPES = ("disease", "gene", "pathway", "process", "cell_type", "anatomy",
                "phenotype", "drug", "therapy", "model_system", "biomarker", "assay",
                "diagnostic", "outcome_measure", "resource", "method")
SOLUTION_TYPES = frozenset({"drug", "therapy", "model_system", "biomarker", "assay",
                            "diagnostic", "outcome_measure", "resource", "method"})
PREDICATES = {
    "treats": "drug/therapy -> disease or phenotype: reported to improve it in patients",
    "rescues": "drug/therapy -> gene, process, phenotype or model_system: corrects a defect "
               "in a model or cells",
    "tested_in": "drug/therapy/assay/outcome_measure -> disease: was applied in a study "
                 "(use effect for the result)",
    "models": "model_system -> disease: recapitulates the disease",
    "biomarker_for": "biomarker -> disease: marks diagnosis, severity or treatment response",
    "diagnoses": "diagnostic/assay/method -> disease: used to diagnose it",
    "measures_outcome_of": "outcome_measure -> disease: used to quantify disease course",
    "targets": "drug/therapy -> gene, pathway or process: acts on it",
    "causes": "gene -> disease: pathogenic variants cause it",
    "involves": "disease -> pathway, process, cell_type or anatomy: mechanism / tissue "
                "involved",
    "has_phenotype": "disease or model_system -> phenotype",
    "shares_mechanism_with": "disease -> disease: same gene, pathway or process affected",
    "applicable_to": "method/resource/model_system -> disease: usable for studying it",
}
EFFECTS = ("positive", "negative", "null", "mixed", "na")
LEVELS = ("clinical_trial", "observational", "case_report", "animal", "in_vitro",
          "in_silico", "review")

SYSTEM = f"""You extract an evidence graph from ONE biomedical paper for a rare-disease
research team that looks for existing solutions (drugs, therapies, disease models, biomarkers,
assays, diagnostics, outcome measures, resources, methods) that could accelerate research on
the input disease, directly or by transfer from a mechanistically related disease.

Rules:
- Extract only what THIS paper states. No outside knowledge, no speculation.
- Prefer the paper's own findings (results, case data). Statements the paper only cites
  from other work may be extracted with evidence_level "review".
- Every edge needs 1-3 evidence items: the passage id ("P12") and an exact, verbatim quote
  copied character for character from that passage (one sentence or clause, max 300
  characters). Never paraphrase a quote. Edges without a verbatim quote will be discarded.
- Name entities precisely and canonically ("PMM2", "epalrestat", "PMM2-CDG",
  "Purkinje cell", "Pmm2 R137H/F115L knock-in mouse"); give common synonyms/abbreviations.
- Record negative and null results too (effect "negative"/"null"); they matter.
- Include the mechanistic links (causes, involves, has_phenotype, shares_mechanism_with)
  that connect the diseases in the paper to genes, pathways, processes, cell types and
  tissues; they decide whether a solution transfers.
- Skip trivia (study logistics, generic statements). Aim for the 5-40 most informative edges.

Entity types: {", ".join(ENTITY_TYPES)}
Predicates:
{chr(10).join(f"- {k}: {v}" for k, v in PREDICATES.items())}
effect: {" | ".join(EFFECTS)} (na for non-outcome edges)
evidence_level: {" | ".join(LEVELS)}

Answer with one JSON object:
{{"summary": "2 sentences: what the paper shows",
  "entities": [{{"key": "e1", "name": "...", "type": "...", "synonyms": ["..."]}}],
  "edges": [{{"subject": "e1", "predicate": "...", "object": "e2", "effect": "...",
             "evidence_level": "...", "organism": "human|mouse|zebrafish|yeast|cells|...",
             "evidence": [{{"passage": "P12", "quote": "..."}}]}}]}}"""


def prompt(profile_text: str, paper: dict, doc) -> str:
    meta = ", ".join(str(x) for x in [paper.get("year"), paper.get("journal"),
                                      "/".join((paper.get("pub_types") or [])[:3])] if x)
    return (f"{profile_text}\n\nPAPER ({doc.source} text{', truncated' if doc.truncated else ''};"
            f" {meta}):\n\n{doc.render()}\n\nExtract the evidence graph of this paper.")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def clean_result(out: dict) -> tuple[dict, list[dict], dict]:
    """(entities by key, edges, counters) with unknown types/predicates/keys removed."""
    stats = {"entities": 0, "edges": 0, "bad_edges": 0}
    ents: dict[str, dict] = {}
    for e in (out or {}).get("entities") or []:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        k = str(e.get("key") or _slug(e["name"]))
        t = e.get("type") if e.get("type") in ENTITY_TYPES else None
        if t is None:
            continue
        ents[k] = {"name": str(e["name"]).strip(), "type": t,
                   "synonyms": [str(s) for s in e.get("synonyms") or []][:8]}
    stats["entities"] = len(ents)
    edges = []
    for r in (out or {}).get("edges") or []:
        if not isinstance(r, dict):
            continue
        s, o, p = str(r.get("subject")), str(r.get("object")), r.get("predicate")
        ev = [x for x in r.get("evidence") or [] if isinstance(x, dict) and x.get("quote")]
        if s not in ents or o not in ents or s == o or p not in PREDICATES or not ev:
            stats["bad_edges"] += 1
            continue
        edges.append({"subject": s, "predicate": p, "object": o,
                      "effect": r.get("effect") if r.get("effect") in EFFECTS else "na",
                      "evidence_level": r.get("evidence_level")
                      if r.get("evidence_level") in LEVELS else "review",
                      "organism": str(r.get("organism") or "")[:40],
                      "evidence": [{"passage": str(x.get("passage") or ""),
                                    "quote": str(x["quote"])[:600]} for x in ev[:3]]})
    stats["edges"] = len(edges)
    return ents, edges, stats
