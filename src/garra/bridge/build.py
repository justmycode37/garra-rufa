"""Offline bridge. Source associations, paper claims and pair inferences stay separate."""
import json
import math
import re
from collections import defaultdict
from itertools import combinations

PREFIXES = {"ORPHANET": "ORPHA", "MIM": "OMIM", "NCBIGENE": "NCBIGene",
            "REACTOME": "Reactome"}
STABLE = {"disease": {"MONDO", "OMIM", "ORPHA", "DOID", "GARD"},
          "gene": {"HGNC", "NCBIGene", "ENSEMBL"}, "phenotype": {"HP"},
          "pathway": {"Reactome", "GO"}, "process": {"GO"}}
CAUSAL = {"caused_by_gene", "caused_by_gene_loss_of_function",
          "caused_by_gene_gain_of_function", "caused_by_somatic_mutation_in"}
HIERARCHY = {"subclass_of", "has_subclass", "subtype_of", "parent_of"}
MECHANISMS = {"pathway", "process"}


def curie(value):
    prefix, sep, local = str(value).strip().partition(":")
    if not sep:
        return str(value)
    prefix = PREFIXES.get(prefix.upper(), prefix.upper())
    if prefix in {"MONDO", "HP", "GARD", "GO"} and local.isdigit():
        local = local.zfill(7)
    return f"{prefix}:{local}"


def publication_ids(p):
    out = []
    pmid = str(p.get("pmid") or "").strip().removeprefix("PMID:")
    if pmid.isdigit() and int(pmid) > 0:
        out.append("PMID:" + str(int(pmid)))
    pmc = str(p.get("pmcid") or "").strip().upper()
    if pmc.startswith("PMC") and pmc[3:].isdigit():
        out.append(pmc)
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi.org/|doi:)", "",
                 str(p.get("doi") or "").strip(), flags=re.I).lower()
    if re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        out.append("DOI:" + doi)
    return out


def build_bridge(source, evidence, *, mappings=(), phenotype_threshold=0.5):
    """Join graph JSON and evidence/main.py JSON; never promote automated claims."""
    if not math.isfinite(phenotype_threshold) or not 0 <= phenotype_threshold <= 1:
        raise ValueError("phenotype_threshold must be between 0 and 1")
    nodes, aliases, issues = {}, {}, []
    for origin, graph in (("source", source), ("evidence", evidence)):
        for n in graph.get("nodes", []):
            raw, kind = n["id"], n["kind"]
            cid = curie(raw)
            trusted = cid.split(":")[0] in STABLE.get(kind, set())
            trusted &= n.get("how") not in {"fuzzy", "text", "pubtator"}
            key = cid if trusted else f"unresolved:{origin}:{raw}"
            if key in nodes and nodes[key]["kind"] != kind:
                issues.append({"reason": "identifier_kind_conflict", "id": cid})
                key = f"unresolved:{origin}:{kind}:{raw}"
            aliases[(origin, raw)] = key
            row = nodes.setdefault(key, {"id": key, "kind": kind, "labels": [],
                                         "origins": [], "identity_verified": bool(trusted)})
            if n.get("label") not in row["labels"]:
                row["labels"].append(n.get("label"))
            if origin not in row["origins"]:
                row["origins"].append(origin)

    # Xrefs and equal names are never equivalence assertions. Explicit mappings need provenance.
    identity_links = []
    for m in mappings:
        a, b = curie(m["from"]), curie(m["to"])
        related = any(e.get("relation") in HIERARCHY | {"related_to", "xref_related"}
                      and {curie(e["from"]), curie(e["to"])} == {a, b}
                      for e in source.get("edges", []))
        if (m.get("relation") != "same_entity" or m.get("reviewed") is not True
                or not m.get("source_url") or a not in nodes or b not in nodes
                or nodes[a]["kind"] != nodes[b]["kind"] or related):
            issues.append({"reason": "mapping_not_verified_or_conflicting", "mapping": m})
            continue
        if a != b:
            for alias, value in list(aliases.items()):
                if value == b:
                    aliases[alias] = a
            for field in ("labels", "origins"):
                nodes[a][field] = sorted(set(nodes[a][field] + nodes[b][field]), key=str)
            del nodes[b]
        identity_links.append(m)

    # Union publication aliases, including bridges between an earlier DOI-only and PMID-only row.
    papers, paper_aliases = {}, {}
    for i, p in enumerate(evidence.get("papers", [])):
        meta = p.get("meta", p)
        ids = publication_ids(meta)
        keys = {paper_aliases[x] for x in ids if x in paper_aliases}
        all_ids = set(ids)
        for k in keys:
            all_ids.update(papers[k]["identifiers"])
        if len({x for x in all_ids if x.startswith("PMID:")}) > 1:
            raise ValueError("Conflicting PMIDs share a publication identifier; review before joining")
        keep = min(keys) if keys else (ids[0] if ids else f"unresolved:paper:{i}")
        row = papers.setdefault(keep, {"id": keep, "identifiers": [], "records": []})
        row["records"].append(p)
        for other in keys - {keep}:
            row["records"].extend(papers.pop(other)["records"])
            for alias, value in list(paper_aliases.items()):
                if value == other:
                    paper_aliases[alias] = keep
        row["identifiers"] = sorted(all_ids)
        for alias in [*all_ids, p.get("key", keep)]:
            paper_aliases[alias] = keep

    claims = []
    for e in evidence.get("edges", []):
        a, b = aliases.get(("evidence", e["from"])), aliases.get(("evidence", e["to"]))
        if not a or not b:
            issues.append({"reason": "missing_claim_entity", "edge": e})
            continue
        for ev in e.get("evidence", []):
            if ev.get("verification") != "exact_passage" or not ev.get("quote"):
                issues.append({"reason": "legacy_or_unverified_passage", "edge": e.get("relation")})
                continue
            paper = paper_aliases.get(ev.get("paper"))
            if not paper:
                issues.append({"reason": "missing_paper", "paper": ev.get("paper")})
                continue
            claims.append({"id": f"claim:{len(claims)+1}", "subject": a, "object": b,
                           "relationship": e["relation"], "paper_id": paper,
                           "direction": ev.get("direction", "unknown"),
                           "polarity": ev.get("polarity", "unknown"),
                           "context": {k: ev.get(k) or "unknown" for k in
                                       ("organism", "tissue", "model", "population", "variant")},
                           "study_type": ev.get("level", "unknown"),
                           "limitations": ev.get("limitations") or ["Not reported/extracted"],
                           "passage": ev["quote"], "locator": {"passage": ev.get("passage"),
                                                                  "section": ev.get("section")},
                           "reviewed": False, "status": "unreviewed_extraction"})

    # Repeated imports of the same publication passage must not inflate evidence counts.
    unique_claims = {}
    for claim in claims:
        key = json.dumps({k: v for k, v in claim.items() if k != "id"}, sort_keys=True)
        unique_claims.setdefault(key, claim)
    claims = [{**c, "id": f"claim:{i + 1}"} for i, c in enumerate(unique_claims.values())]

    profiles = {k: {"phenotype": set(), "mechanism": set(), "genes": set()}
                for k, n in nodes.items() if n["kind"] == "disease" and n["identity_verified"]}
    relations, paths = [], []
    gene_processes = defaultdict(set)
    for e in source.get("edges", []):
        a, b = aliases.get(("source", e["from"])), aliases.get(("source", e["to"]))
        if not a or not b:
            continue
        rel = e["relation"]
        relations.append({**e, "from": a, "to": b})
        if a in profiles and rel == "has_phenotype" and nodes[b]["kind"] == "phenotype":
            profiles[a]["phenotype"].add(b)
        if a in profiles and nodes[b]["kind"] == "gene" and rel in CAUSAL | {"associated_target"}:
            profiles[a]["genes"].add(b)
        if b in profiles and nodes[a]["kind"] == "gene" and rel in {"causes", "causes_disease"}:
            profiles[b]["genes"].add(a)
        if nodes[b]["kind"] in MECHANISMS and rel in {"in_pathway", "involved_in", "involves"}:
            if nodes[a]["kind"] == "gene":
                gene_processes[a].add(b)
            elif a in profiles:
                profiles[a]["mechanism"].add(b)
        paths.append({"from": a, "to": b, "relation": rel, "source": e.get("source"),
                      **{k: e[k] for k in ("provenance", "gene_mapping") if k in e}})
    for c in claims:
        if (nodes[c["subject"]]["kind"] == "gene"
                and nodes[c["object"]]["kind"] in MECHANISMS
                and c["relationship"] == "participates_in"
                and c["polarity"] == "asserted"):
            gene_processes[c["subject"]].add(c["object"])
    for d, profile in profiles.items():
        for gene in profile["genes"]:
            profile["mechanism"].update(gene_processes[gene])
    for c in claims:
        if (c["subject"] in profiles and nodes[c["object"]]["identity_verified"]
                and nodes[c["object"]]["kind"] in MECHANISMS
                and c["relationship"] in {"involves", "disrupts", "affects"}):
            profiles[c["subject"]]["mechanism"].add(c["object"])

    # Inverted index avoids scanning every pair in a large disease catalogue.
    index = defaultdict(set)
    for d, p in profiles.items():
        for mode in ("phenotype", "mechanism"):
            for feature in p[mode]:
                if nodes[feature]["identity_verified"]:
                    index[(mode, feature)].add(d)
    pairs = {pair for ds in index.values() for pair in combinations(sorted(ds), 2)}
    candidates = []
    for a, b in sorted(pairs):
        pa, pb = profiles[a], profiles[b]
        union = pa["phenotype"] | pb["phenotype"]
        overlap = pa["phenotype"] & pb["phenotype"]
        score = len(overlap) / len(union) if pa["phenotype"] and pb["phenotype"] else None
        shared = sorted(pa["mechanism"] & pb["mechanism"])
        reasons = []
        if score is not None and overlap and score >= phenotype_threshold:
            reasons.append("phenotype_overlap")
        if shared:
            reasons.append("shared_pathway_or_process")
        if not reasons:
            continue
        relevant = [c for c in claims if c["subject"] in {a, b} and c["object"] in shared
                    and c["relationship"] in {"involves", "disrupts", "affects"}]
        supporting, contradicting, comparisons = set(), set(), []
        for x in relevant:
            for y in relevant:
                if x["subject"] != a or y["subject"] != b or x["object"] != y["object"]:
                    continue
                known = {"increased", "decreased", "unchanged"}
                comparable = x["direction"] in known and y["direction"] in known
                asserted = x["polarity"] == y["polarity"] == "asserted"
                differences = [k for k in x["context"] if x["context"][k] != "unknown"
                               and y["context"][k] != "unknown"
                               and x["context"][k] != y["context"][k]]
                same = comparable and asserted and x["direction"] == y["direction"]
                opposite = comparable and asserted and x["direction"] != y["direction"]
                negated = (comparable and x["direction"] == y["direction"]
                           and {x["polarity"], y["polarity"]} == {"asserted", "negated"})
                if same:
                    supporting.update((x["id"], y["id"]))
                if opposite or negated:
                    contradicting.update((x["id"], y["id"]))
                comparisons.append({"claim_ids": [x["id"], y["id"]],
                                    "shared_process": x["object"], "same_direction": same,
                                    "potential_conflict": opposite or negated,
                                    "context_differences": differences,
                                    "context_unknown": [k for k in x["context"]
                                                        if "unknown" in (x["context"][k], y["context"][k])]})
        if supporting:
            reasons.append("paper_reported_effect_alignment")
        candidates.append({"disease_a": a, "disease_b": b, "candidate_reasons": reasons,
                           "phenotype_similarity": {"metric": "exact_hpo_jaccard",
                                                    "value": score, "shared_ids": sorted(overlap),
                                                    "is_probability": False},
                           "shared_process_ids": shared,
                           "candidate_claim_ids": sorted({c["id"] for c in claims
                               if c["object"] in shared and c["subject"] in
                               {a, b, *pa["genes"], *pb["genes"]}}),
                           "source_paths": [p for p in paths if p["from"] in
                                            {a, b, *pa["genes"], *pb["genes"]}],
                           "assessment": {"status": "conflicting_evidence" if contradicting else
                                          "hypothesis" if supporting else "insufficient_evidence",
                                          "inferred": True, "reviewed": False,
                                          "supporting_claim_ids": sorted(supporting),
                                          "contradicting_claim_ids": sorted(contradicting),
                                          "comparisons": comparisons,
                                          "unknowns": ["Passage matching does not validate claim meaning.",
                                                       "Compatible disease mechanisms require review.",
                                                       "Research or treatment transfer is not established."]}})
    return {"schema_version": 1, "nodes": list(nodes.values()), "identity_links": identity_links,
            "source_relations": relations, "papers": list(papers.values()), "claims": claims,
            "candidates": candidates, "issues": issues,
            "policy": "No name-only merges, no inferred probabilities, no automatic review."}
