"""Offline discovery over a bridge snapshot; clustering never validates a mechanism."""

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from copy import deepcopy
from itertools import combinations
from pathlib import Path

from garra.ui.service import InputError

KINDS = {"disease", "gene", "phenotype", "pathway", "process", "anatomy"}


class DiscoveryEngine:
    def __init__(self, bridge=None, *, atlas=None, phenotype_threshold=0.5):
        if (
            isinstance(phenotype_threshold, bool)
            or not isinstance(phenotype_threshold, (int, float))
            or not math.isfinite(phenotype_threshold)
            or not 0 < phenotype_threshold <= 1
        ):
            raise ValueError("phenotype_threshold must be in (0, 1]")
        self.threshold = phenotype_threshold
        bridge = deepcopy(
            bridge
            if bridge is not None
            else {"schema_version": 1, "nodes": [], "candidates": [], "claims": [], "papers": []}
        )
        if (
            not isinstance(bridge, dict)
            or type(bridge.get("schema_version")) is not int
            or bridge["schema_version"] != 1
        ):
            raise ValueError("Expected a version-1 bridge snapshot")
        self.nodes = self._index(bridge.get("nodes"), "nodes")
        self.claims = self._index(bridge.get("claims"), "claims")
        self.papers = self._index(bridge.get("papers"), "papers")
        self.pairs = {}
        for node in self.nodes.values():
            if not isinstance(node.get("kind"), str):
                raise ValueError("Node kind is required")
            labels = node.get("labels", [])
            if not isinstance(labels, list) or any(not isinstance(x, str) for x in labels):
                raise ValueError("Node labels must be strings")
        candidates = bridge.get("candidates")
        if not isinstance(candidates, list):
            raise ValueError("candidates must be a list")
        for pair in candidates:
            a, b = pair["disease_a"], pair["disease_b"]
            if a == b or any(
                x not in self.nodes
                or self.nodes[x]["kind"] != "disease"
                or self.nodes[x].get("identity_verified") is not True
                for x in (a, b)
            ):
                raise ValueError("Pairs require two verified disease identities")
            key = tuple(sorted((a, b)))
            if key in self.pairs:
                raise ValueError("Duplicate disease pair")
            for process in pair["shared_process_ids"]:
                if process not in self.nodes or self.nodes[process]["kind"] not in {
                    "pathway",
                    "process",
                }:
                    raise ValueError("Unknown process in pair")
            value = pair["phenotype_similarity"]["value"]
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (float, int))
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError("Invalid phenotype similarity")
            refs = set(pair.get("candidate_claim_ids", []))
            for field in ("supporting_claim_ids", "contradicting_claim_ids"):
                refs.update(pair["assessment"].get(field, []))
            if refs - self.claims.keys():
                raise ValueError("Pair references missing claims")
            self.pairs[key] = pair
        for claim in self.claims.values():
            if claim["paper_id"] not in self.papers:
                raise ValueError("Claim references missing publication")
        self.groups = self._cluster()
        self.entities = deepcopy(self.nodes)
        self.atlas_loaded = atlas is not None
        if atlas is not None:
            self._load_atlas(Path(atlas))
        self.version = hashlib.sha256(
            json.dumps({"bridge": bridge, "threshold": self.threshold}, sort_keys=True).encode()
        ).hexdigest()[:16]
        self.memberships = defaultdict(list)
        for group in self.groups.values():
            for member in group["member_ids"]:
                self.memberships[member].append(group["id"])

    @staticmethod
    def _index(rows, name):
        if not isinstance(rows, list):
            raise ValueError(f"{name} must be a list")
        result = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                raise ValueError(f"{name} records require string IDs")
            if row["id"] in result:
                raise ValueError(f"Duplicate {name} ID")
            result[row["id"]] = row
        return result

    def _load_atlas(self, path):
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            for table, column, label, kind in (
                ("disease", "disease_key", "primary_name", "disease"),
                ("gene", "gene_key", "symbol", "gene"),
                ("phenotype", "hpo_id", "label", "phenotype"),
                ("pathway", "pathway_key", "label", "pathway"),
            ):
                for identifier, name in conn.execute(f"SELECT {column}, {label} FROM {table}"):
                    row = self.entities.setdefault(
                        identifier, {"id": identifier, "kind": kind, "labels": []}
                    )
                    if name and name not in row["labels"]:
                        row["labels"].append(name)
            for table, column in (("disease_alias", "disease_key"), ("gene_alias", "gene_key")):
                for alias, identifier in conn.execute(
                    f"SELECT alias_normalized, {column} FROM {table}"
                ):
                    if (
                        identifier in self.entities
                        and alias not in self.entities[identifier]["labels"]
                    ):
                        self.entities[identifier]["labels"].append(alias)
        finally:
            conn.close()

    def _cluster(self):
        groups = {}
        by_process = defaultdict(set)
        for (a, b), pair in self.pairs.items():
            for process in pair["shared_process_ids"]:
                by_process[process].update((a, b))
        memberships = [
            ("pathway", process, sorted(members)) for process, members in sorted(by_process.items())
        ]
        # Deterministic complete-link agglomeration. Every pair in a phenotype group
        # must meet the threshold; a chain A-B-C does not imply similarity of A-C.
        blocks = {x: {x} for pair in self.pairs for x in pair}
        eligible = []
        for key, pair in self.pairs.items():
            score = pair["phenotype_similarity"]["value"]
            if (
                pair["phenotype_similarity"].get("metric") == "exact_hpo_jaccard"
                and score is not None
                and score >= self.threshold
            ):
                eligible.append((-score, key))
        allowed = {key for _, key in eligible}
        for _, (a, b) in sorted(eligible):
            left, right = blocks[a], blocks[b]
            if left is right:
                continue
            if all(tuple(sorted((x, y))) in allowed for x in left for y in right):
                merged = left | right
                for member in merged:
                    blocks[member] = merged
        seen = set()
        for block in blocks.values():
            members = tuple(sorted(block))
            if len(members) > 1 and members not in seen:
                seen.add(members)
                memberships.append(("phenotype", None, list(members)))
        for kind, process, members in memberships:
            digest = hashlib.sha256(json.dumps([kind, process, members]).encode()).hexdigest()[:20]
            identifier = f"{kind}-{digest}"
            pairs = [
                self.pairs[k]
                for k in combinations(members, 2)
                if k in self.pairs
                and (kind == "phenotype" or process in self.pairs[k]["shared_process_ids"])
            ]
            groups[identifier] = {
                "id": identifier,
                "kind": kind,
                "process_id": process,
                "member_ids": members,
                "label": (
                    (self.nodes[process].get("labels") or [process])[0]
                    if process
                    else "Phenotype similarity group"
                ),
                "method": "shared_process_membership" if process else "deterministic_complete_link",
                "threshold": self.threshold if kind == "phenotype" else None,
                "status": "candidate_group_not_validated_mechanism",
                "explanation": (
                    "Members share a process in candidate connections; effects may differ."
                    if process
                    else "Every member pair meets the exact-HPO Jaccard threshold."
                ),
                "pairs": pairs,
            }
        return groups

    def metadata(self):
        return {
            "schema_version": 1,
            "snapshot": self.version,
            "coverage": {
                "search_entities": len(self.entities),
                "bridge_entities": len(self.nodes),
                "candidate_pairs": len(self.pairs),
                "clusters": len(self.groups),
                "atlas_loaded": self.atlas_loaded,
            },
            "notice": "Research discovery only. Cluster membership is not proof of a shared mechanism. "
            "Only supplied bridge candidates can form groups; absent data is not negative evidence.",
        }

    def _card(self, identifier):
        node = self.entities[identifier]
        return {
            "id": identifier,
            "name": (node.get("labels") or [identifier])[0],
            "kind": node["kind"],
            "cluster_ids": self.memberships.get(identifier, []),
            "bridge_available": identifier in self.nodes,
        }

    def search(self, body):
        if not isinstance(body, dict) or set(body) - {"mode", "query", "kind", "limit", "offset"}:
            raise InputError("Text search accepts mode, query, kind, limit, offset")
        if body.get("mode", "text") != "text":
            raise InputError("Unsupported search mode")
        query = body.get("query")
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise InputError("query must contain 1–200 characters")
        kind = body.get("kind")
        if kind is not None and (not isinstance(kind, str) or kind not in KINDS):
            raise InputError("Unsupported entity kind")
        limit, offset = body.get("limit", 20), body.get("offset", 0)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise InputError("limit must be 1–100 and offset a nonnegative integer")
        q = " ".join(query.casefold().split())
        hits = []
        for identifier, node in self.entities.items():
            if kind is not None and kind != node["kind"]:
                continue
            labels = [" ".join(x.casefold().split()) for x in node.get("labels", [])]
            values = [identifier.casefold(), *labels]
            rank = (
                0
                if q == values[0]
                else 1
                if q in labels
                else 2
                if any(x.startswith(q) for x in values)
                else 3
                if any(q in x for x in values)
                else None
            )
            if rank is not None:
                hits.append((rank, identifier))
        hits.sort()
        return {
            **self.metadata(),
            "mode": "text",
            "query": query,
            "total": len(hits),
            "ambiguous_exact_match": sum(rank <= 1 for rank, _ in hits) > 1,
            "offset": offset,
            "limit": limit,
            "results": [
                {
                    **self._card(identifier),
                    "match": ["exact_id", "exact_label_or_alias", "prefix", "substring"][rank],
                }
                for rank, identifier in hits[offset : offset + limit]
            ],
        }

    def clusters(self, *, kind=None, entity_id=None):
        if kind is not None and kind not in {"pathway", "phenotype"}:
            raise InputError("Cluster kind must be pathway or phenotype")
        items = [
            {k: deepcopy(v) for k, v in row.items() if k != "pairs"}
            for row in sorted(self.groups.values(), key=lambda g: g["id"])
            if (kind is None or row["kind"] == kind)
            and (
                entity_id is None
                or entity_id in row["member_ids"]
                or entity_id == row["process_id"]
            )
        ]
        return {**self.metadata(), "clusters": items, "total": len(items)}

    def cluster(self, identifier):
        if identifier not in self.groups:
            return None
        group = deepcopy(self.groups[identifier])
        refs = set()
        for pair in group["pairs"]:
            refs.update(pair.get("candidate_claim_ids", []))
            for field in ("supporting_claim_ids", "contradicting_claim_ids"):
                refs.update(pair["assessment"].get(field, []))
        claims = [deepcopy(self.claims[x]) for x in sorted(refs)]
        papers = [deepcopy(self.papers[x]) for x in sorted({c["paper_id"] for c in claims})]
        return {
            **self.metadata(),
            "cluster": group,
            "members": [self._card(x) for x in group["member_ids"]],
            "claims": claims,
            "papers": papers,
        }
