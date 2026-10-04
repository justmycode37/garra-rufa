"""Build a multi-disease snapshot from saved raw associations, retaining provenance."""

import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from garra.bridge import build_bridge
from garra.discovery import DiscoveryEngine


def verify_gene(identifier):
    url = "https://rest.genenames.org/fetch/ensembl_gene_id/" + quote(identifier, safe="")
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=25) as response:
            data = json.load(response)
        docs = [
            d
            for d in data.get("response", {}).get("docs", [])
            if d.get("ensembl_gene_id") == identifier and d.get("status") == "Approved"
        ]
        if len(docs) != 1 or not re.fullmatch(r"HGNC:\d+", docs[0].get("hgnc_id", "")):
            return identifier, {"error": "No unique approved exact identifier match", "url": url}
        return identifier, {
            "hgnc_id": docs[0]["hgnc_id"],
            "symbol": docs[0]["symbol"],
            "url": url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }
    except (OSError, ValueError) as exc:
        return identifier, {"error": str(exc), "url": url}


def collect(cache, gene_mappings):
    nodes, edges, inputs, warnings = {}, {}, [], []

    def node(identifier, label, kind):
        nodes.setdefault(identifier, {"id": identifier, "label": label or identifier, "kind": kind})

    def edge(a, b, relation, source, path, retrieved_at):
        key = (a, b, relation, source)
        item = edges.setdefault(
            key, {"from": a, "to": b, "relation": relation, "source": source, "provenance": []}
        )
        provenance = {"file": str(path.relative_to(cache)), "retrieved_at": retrieved_at}
        if provenance not in item["provenance"]:
            item["provenance"].append(provenance)

    paths = sorted(
        set(cache.glob("**/monarch_*phenotypes.json"))
        | set(cache.glob("**/monarch_*genes.json"))
        | set(cache.glob("**/opentargets/*.json"))
    )
    for path in paths:
        raw = path.read_bytes()
        inputs.append(
            {"file": str(path.relative_to(cache)), "sha256": hashlib.sha256(raw).hexdigest()}
        )
        envelope = json.loads(raw)
        body = envelope.get("body", envelope)
        timestamp = body.get("retrieved_at", envelope.get("retrieved_at"))
        if path.parent.name == "opentargets":
            ensembl = body.get("ensembl_id")
            mapping = gene_mappings.get(ensembl, {})
            if not body.get("pathways"):
                continue
            if not mapping.get("hgnc_id"):
                warnings.append(
                    {"file": str(path.relative_to(cache)), "reason": "unverified_gene_mapping"}
                )
                continue
            gene = mapping["hgnc_id"]
            node(gene, mapping.get("symbol"), "gene")
            for pathway in body["pathways"]:
                identifier = pathway.get("pathwayId", "")
                if not re.fullmatch(r"R-HSA-\d+", identifier):
                    continue
                process = "Reactome:" + identifier
                node(process, pathway.get("pathway"), "pathway")
                edge(gene, process, "in_pathway", "Open Targets / Reactome", path, timestamp)
                edges[(gene, process, "in_pathway", "Open Targets / Reactome")]["gene_mapping"] = (
                    mapping
                )
            continue
        raw_result = body.get("raw", {})
        items = raw_result.get("items", [])
        if raw_result.get("total", len(items)) > len(items):
            warnings.append(
                {
                    "file": str(path.relative_to(cache)),
                    "reason": "truncated_source_response",
                    "loaded": len(items),
                    "total": raw_result["total"],
                }
            )
        for item in items:
            if item.get("negated"):
                continue
            a, b = item.get("subject", ""), item.get("object", "")
            predicate = item.get("predicate")
            source = item.get("primary_knowledge_source") or "Monarch Initiative"
            if not isinstance(source, str):
                source = json.dumps(source, sort_keys=True)
            if (
                predicate == "biolink:has_phenotype"
                and re.fullmatch(r"MONDO:\d+", a)
                and re.fullmatch(r"HP:\d+", b)
            ):
                node(a, item.get("subject_label"), "disease")
                node(b, item.get("object_label"), "phenotype")
                edge(a, b, "has_phenotype", source, path, timestamp)
            elif (
                predicate == "biolink:causes"
                and re.fullmatch(r"HGNC:\d+", a)
                and re.fullmatch(r"MONDO:\d+", b)
            ):
                node(a, item.get("subject_label"), "gene")
                node(b, item.get("object_label"), "disease")
                edge(a, b, "causes", source, path, timestamp)
    return (
        {
            "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
            "edges": sorted(
                edges.values(), key=lambda e: (e["from"], e["to"], e["relation"], e["source"])
            ),
        },
        inputs,
        warnings,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("data/cache/connections"))
    parser.add_argument("--out", type=Path, default=Path("data/derived/discovery"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument(
        "--verify-genes", action="store_true", help="Query HGNC for exact Ensembl identity mappings"
    )
    args = parser.parse_args(argv)
    if not args.cache.is_dir():
        parser.error("Cache directory does not exist")
    args.out.mkdir(parents=True, exist_ok=True)
    mapping_path = args.out / "gene-mappings.json"
    mappings = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
    if args.verify_genes:
        targets = set()
        for path in args.cache.glob("**/opentargets/*.json"):
            data = json.loads(path.read_text())
            body = data.get("body", data)
            if body.get("pathways") and re.fullmatch(r"ENSG\d+", body.get("ensembl_id", "")):
                targets.add(body["ensembl_id"])
        pending = sorted(x for x in targets if not mappings.get(x, {}).get("hgnc_id"))
        print(f"Verifying {len(pending)} gene identifiers with HGNC", flush=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for identifier, result in pool.map(verify_gene, pending):
                mappings[identifier] = result
        mapping_path.write_text(json.dumps(mappings, indent=2) + "\n")
    source, inputs, warnings = collect(args.cache, mappings)
    evidence = (
        json.loads(args.evidence.read_text())
        if args.evidence
        else {"nodes": [], "edges": [], "papers": []}
    )
    bridge = build_bridge(source, evidence, phenotype_threshold=0.5)
    engine = DiscoveryEngine(bridge)
    clusters = engine.clusters()
    counts = {
        kind: sum(g["kind"] == kind for g in clusters["clusters"])
        for kind in ("pathway", "phenotype")
    }
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "inputs": inputs,
        "gene_mapping_sha256": hashlib.sha256(
            json.dumps(mappings, sort_keys=True).encode()
        ).hexdigest(),
        "evidence_file": str(args.evidence) if args.evidence else None,
        "evidence_sha256": hashlib.sha256(args.evidence.read_bytes()).hexdigest()
        if args.evidence
        else None,
        "warnings": warnings,
        "coverage": engine.metadata()["coverage"],
        "diseases": sum(n["kind"] == "disease" for n in source["nodes"]),
        "source_edges": len(source["edges"]),
        "cluster_counts": counts,
        "notice": "Source-based candidate groups, not validated shared disease mechanisms. "
        "Source response truncation and collection scope limit completeness.",
    }
    for name, value in [
        ("source.json", source),
        ("bridge.json", bridge),
        ("clusters.json", clusters),
        ("manifest.json", manifest),
    ]:
        (args.out / name).write_text(json.dumps(value, indent=2) + "\n")
    print(
        json.dumps(
            {k: manifest[k] for k in ("diseases", "source_edges", "coverage", "cluster_counts")}
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
