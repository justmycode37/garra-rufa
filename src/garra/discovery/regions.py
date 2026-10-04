"""Anatomical overlays on real disease clusters, supported by HPO annotation paths."""

import hashlib
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from garra.ui.service import InputError


class RegionIndex:
    def __init__(self, engine, bridge, definitions, ontology):
        self.engine = engine
        self.definitions = definitions
        raw = ontology.read_bytes()
        self.ontology_hash = hashlib.sha256(raw).hexdigest()
        self.parents, self.labels = defaultdict(set), {}
        for block in raw.decode().split("[Term]")[1:]:
            if re.search(r"^is_obsolete: true$", block, re.M):
                continue
            identifier = re.search(r"^id: (HP:\d+)$", block, re.M)
            label = re.search(r"^name: (.+)$", block, re.M)
            if identifier and label:
                self.labels[identifier[1]] = label[1]
                self.parents[identifier[1]].update(re.findall(r"^is_a: (HP:\d+)", block, re.M))
        for definition in definitions.values():
            if definition["hpo"] not in self.labels:
                raise ValueError("Unknown body-region HPO root: " + definition["hpo"])
        self.matches = {key: defaultdict(set) for key in definitions}
        ancestry = {}
        self.profiles = defaultdict(set)
        self.expanded_profiles = defaultdict(set)
        for edge in bridge.get("source_relations", []):
            if edge["relation"] != "has_phenotype":
                continue
            disease, hp = edge["from"], edge["to"]
            node = engine.nodes.get(disease, {})
            if node.get("kind") != "disease" or node.get("identity_verified") is not True:
                continue
            if hp not in ancestry:
                ancestors, pending = set(), [hp]
                while pending:
                    current = pending.pop()
                    if current in ancestors:
                        continue
                    ancestors.add(current)
                    pending.extend(self.parents[current])
                ancestry[hp] = ancestors
            self.profiles[disease].add(hp)
            self.expanded_profiles[disease].update(ancestry[hp])
            for key, definition in definitions.items():
                if definition["hpo"] in ancestry[hp]:
                    self.matches[key][disease].add(hp)
        frequency = Counter(term for terms in self.expanded_profiles.values() for term in terms)
        population = len(self.expanded_profiles)
        self.weights = {term: math.log(population / count) for term, count in frequency.items()}
        self.built_at = datetime.now(timezone.utc).isoformat()

    def neighbors(self, identifier):
        if identifier not in self.engine.entities:
            raise InputError("Unknown entity identifier")
        profile = self.expanded_profiles.get(identifier, set())
        candidates = []
        for other, terms in self.expanded_profiles.items():
            if other == identifier or not profile:
                continue
            shared = {t for t in profile & terms if self.weights[t] > 0}
            denominator = sum(self.weights[t] for t in profile | terms)
            if not shared or denominator == 0:
                continue
            exact_a, exact_b = self.profiles[identifier], self.profiles[other]
            candidates.append({
                **self.engine._card(other),
                "score": sum(self.weights[t] for t in shared) / denominator,
                "exact_hpo_jaccard": len(exact_a & exact_b) / len(exact_a | exact_b),
                "shared_features": [{"id": t, "label": self.labels.get(t, t),
                                     "exact_in_both": t in exact_a & exact_b}
                                    for t in sorted(shared, key=lambda t: (-self.weights[t], t))[:5]],
                "status": "exploratory_candidate_not_validated_mechanism",
            })
        candidates.sort(key=lambda c: (-c["score"], c["id"]))
        return {
            **self.engine.metadata(), "entity_id": identifier,
            "annotation_status": "loaded" if profile else "not_annotated_in_snapshot",
            "metric": "ancestor_information_weighted_jaccard", "is_probability": False,
            "ontology_sha256": self.ontology_hash, "total": len(candidates), "candidates": candidates[:10],
            "notice": "Exploratory ranking using HPO ancestors and information weights from this snapshot. "
                      "Universal terms have zero weight. Scores change with coverage, are not probabilities, "
                      "and do not use the exact-HPO 50% cluster cutoff. Membership in existing clusters is unchanged.",
        }

    def catalog(self):
        return {
            "regions": [
                {
                    **value,
                    "id": key,
                    "hpo_label": self.labels[value["hpo"]],
                    "disease_count": len(self.matches[key]),
                }
                for key, value in self.definitions.items()
            ],
            "ontology_sha256": self.ontology_hash,
            "notice": "Anatomical discovery filters, not patient findings. Region assignments require clinical review.",
        }

    def detail(self, key):
        if key not in self.definitions:
            raise InputError("Unknown body region")
        matches = self.matches[key]
        groups = self.engine.clusters()["clusters"]
        groups = [
            {**g, "region_member_ids": sorted(set(g["member_ids"]) & matches.keys())}
            for g in groups
            if set(g["member_ids"]) & matches.keys()
        ]
        related = []
        for other, definition in self.definitions.items():
            if other == key:
                continue
            pairs = [
                list(pair)
                for pair in self.engine.pairs
                if (
                    (pair[0] in matches and pair[1] in self.matches[other])
                    or (pair[1] in matches and pair[0] in self.matches[other])
                )
            ]
            if pairs:
                related.append(
                    {
                        "id": other,
                        "label": definition["label"],
                        "pair_count": len(pairs),
                        "example_pairs": pairs[:5],
                    }
                )
        return {
            **self.engine.metadata(),
            "region": {**self.definitions[key], "id": key},
            "ontology_sha256": self.ontology_hash,
            "diseases": [
                {
                    **self.engine._card(d),
                    "matched_phenotypes": [
                        {
                            "id": hp,
                            "label": self.labels.get(hp, hp),
                            "root": self.definitions[key]["hpo"],
                            "relation": "is_a_descendant_or_self",
                        }
                        for hp in sorted(hps)
                    ],
                }
                for d, hps in sorted(matches.items())
            ],
            "clusters": groups,
            "related_regions": sorted(related, key=lambda r: (-r["pair_count"], r["id"])),
            "notice": "These regions contain diseases joined by candidate connections. "
            "This does not establish organ-to-organ causation or a shared mechanism. "
            "No annotation in this snapshot means unknown, not unaffected.",
        }

    def atlas(self, body):
        if not isinstance(body, dict) or set(body) - {"region", "offset", "limit", "query"}:
            raise InputError("Use region, offset, limit and query only")
        key = next((k for k, v in self.definitions.items() if v["hpo"] == body.get("region")), None)
        if key is None:
            raise InputError("Unknown anatomical HPO term")
        offset, limit, query = body.get("offset", 0), body.get("limit", 6), body.get("query", "")
        if (
            type(offset) is not int
            or not 0 <= offset <= 100000
            or type(limit) is not int
            or not 1 <= limit <= 12
        ):
            raise InputError("Invalid atlas page")
        if not isinstance(query, str) or len(query) > 120:
            raise InputError("Invalid atlas filter")
        matches = self.matches[key]
        ids = [
            d
            for d in sorted(matches)
            if query.casefold() in d.casefold()
            or query.casefold() in self.engine._card(d)["name"].casefold()
        ]
        root = self.definitions[key]["hpo"]

        def hpnode(hp):
            return {
                "id": hp,
                "label": self.labels[hp],
                "kind": "phenotype",
                "providers": ["HPO"],
                "url": "https://hpo.jax.org/browse/term/" + hp,
            }

        nodes, edges = {root: hpnode(root)}, []
        for disease in ids[offset : offset + limit]:
            nodes[disease] = {
                "id": disease,
                "label": self.engine._card(disease)["name"],
                "kind": "disease",
                "providers": ["Monarch snapshot"],
            }
            for hp in sorted(matches[disease]):
                nodes[hp] = hpnode(hp)
                if hp != root:
                    edges.append(
                        {
                            "from": root,
                            "to": hp,
                            "relation": "has_descendant",
                            "source": "HPO",
                            "evidence": [],
                        }
                    )
                edges.append(
                    {
                        "from": disease,
                        "to": hp,
                        "relation": "has_phenotype",
                        "source": "Monarch snapshot",
                        "evidence": [],
                    }
                )
        # Include available source connections and paper claims for this disease page.
        # Counts remain local to this page, not the full regional catalogue.
        for disease in ids[offset : offset + limit]:
            graph = self.engine.entity_graph(disease)["graph"]
            for node in graph["nodes"]:
                nodes[node["id"]] = {**nodes.get(node["id"], {}), **node}
            edges.extend(graph["edges"])
        edges = list({(e["from"], e["to"], e["relation"], e.get("source")): e for e in edges}.values())
        return {
            "status": "ok" if ids else "empty",
            "root": root,
            "nodes": list(nodes.values()),
            "edges": edges,
            "total": len(ids),
            "offset": offset,
            "nextOffset": offset + limit if offset + limit < len(ids) else None,
            "datasetVersion": self.engine.version,
            "retrievedAt": self.built_at,
            "unavailableProviders": [],
            "notice": "Loaded multi-disease snapshot only. HPO descendants identify regional annotations; not diagnosis.",
        }
