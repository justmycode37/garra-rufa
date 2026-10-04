"""Combine saved extractor outputs without executing HTML or inventing paper claims."""
import hashlib
import json
from copy import deepcopy

from garra.bridge.build import STABLE, curie


def load_evidence(paths):
    documents, inventory, seen = [], [], set()
    for path in sorted(set(paths)):
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen:
            inventory.append({"file": str(path), "sha256": digest, "status": "duplicate_file"})
            continue
        doc = json.loads(raw)
        if not isinstance(doc, dict) or any(not isinstance(doc.get(k), list) for k in ("nodes", "edges", "papers")):
            raise ValueError(f"Expected an extracted evidence graph: {path}")
        documents.append((digest, doc))
        seen.add(digest)
        inventory.append({"file": str(path), "sha256": digest, "status": "loaded",
                          "paper_records": len(doc["papers"]), "claim_edges": len(doc["edges"])})
    if len(documents) == 1:
        return documents[0][1], inventory
    nodes, edges, papers = {}, [], []
    for digest, original in documents:
        doc = deepcopy(original)
        aliases, paper_keys = {}, {}
        for node in doc["nodes"]:
            identifier = curie(node["id"])
            trusted = identifier.split(":")[0] in STABLE.get(node["kind"], set()) and node.get("how") not in {"text", "fuzzy", "pubtator"}
            key = identifier if trusted else f"import:{digest[:16]}:{node['id']}"
            aliases[node["id"]] = key
            if key in nodes and nodes[key]["kind"] != node["kind"]:
                raise ValueError("Conflicting evidence entity kinds: " + key)
            nodes.setdefault(key, {**node, "id": key})
        for index, paper in enumerate(doc["papers"]):
            old = paper.get("key", str(index))
            key = f"import:{digest[:16]}:{old}"
            paper_keys[old] = key
            papers.append({**paper, "key": key})
        for edge in doc["edges"]:
            if edge["from"] not in aliases or edge["to"] not in aliases:
                raise ValueError("Evidence edge has a missing endpoint")
            edge["from"], edge["to"] = aliases[edge["from"]], aliases[edge["to"]]
            for citation in edge.get("evidence", []):
                citation["paper"] = paper_keys.get(citation.get("paper"), citation.get("paper"))
            edges.append(edge)
    return {"nodes": list(nodes.values()), "edges": edges, "papers": papers}, inventory


def load_literature(paths):
    """Retain downloaded citations and their explicit search-hit entity IDs, not claims."""
    from garra.bridge.build import publication_ids

    records, inventory, seen = [], [], set()
    for path in sorted(set(paths)):
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        doc = json.loads(raw)
        if not isinstance(doc, dict) or not isinstance(doc.get("papers"), list):
            raise ValueError("Expected downloaded literature JSON: " + str(path))
        inventory.append({"file": str(path), "sha256": digest, "paper_records": len(doc["papers"])})
        for i, paper in enumerate(doc["papers"]):
            ids = publication_ids(paper)
            records.append({"key": ids[0] if ids else f"unresolved:download:{digest[:16]}:{i}",
                            "meta": paper, "import_file": str(path), "record_status": "literature_search_result"})
    return records, inventory
