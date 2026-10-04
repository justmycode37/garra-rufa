"""Anatomical disease neighbourhoods from the repository's downloaded HPO data.

No inferred diagnosis or fuzzy name matching: every disease joins through a
positive HPO annotation to the selected term or one of its ontology descendants.
"""
import csv
import re
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from garra.datasets import ensure
from garra.ui.service import InputError

HPO_URL = "https://hpo.jax.org/browse/term/"
ROOT = Path(__file__).resolve().parents[3]


class AtlasIndex:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else ROOT / "data" / "hpo"
        self._lock = threading.Lock()
        self._loaded = False

    def _load(self):
        with self._lock:
            if self._loaded:
                return
            # Missing files are fetched from the HPO release; if that fails the read
            # below raises OSError and the route answers 503.
            ensure(self.directory / "hp.obo")
            ensure(self.directory / "phenotype.hpoa")
            terms, children = {}, defaultdict(set)
            for block in (self.directory / "hp.obo").read_text().split("[Term]")[1:]:
                fields = defaultdict(list)
                for line in block.splitlines():
                    if ": " in line:
                        key, value = line.split(": ", 1)
                        fields[key].append(value)
                if not fields["id"] or fields["is_obsolete"] == ["true"]:
                    continue
                ident = fields["id"][0]
                definition = (fields["def"] or [""])[0]
                match = re.match(r'"(.*)"\s*\[', definition)
                terms[ident] = {"id": ident, "label": (fields["name"] or [ident])[0],
                                "kind": "phenotype", "description": match[1] if match else "",
                                "url": HPO_URL + ident, "providers": ["HPO"]}
                for parent in fields["is_a"]:
                    children[parent.split()[0]].add(ident)
            annotations, names, version = defaultdict(dict), {}, ""
            with (self.directory / "phenotype.hpoa").open() as handle:
                def records():
                    nonlocal version
                    for line in handle:
                        if line.startswith("#version:"):
                            version = line.partition(":")[2].strip()
                        if not line.startswith("#"):
                            yield line
                for row in csv.DictReader(records(), delimiter="\t"):
                    if row.get("qualifier") == "NOT" or row.get("aspect") not in (None, "", "P"):
                        continue
                    hp, disease = row["hpo_id"], row["database_id"]
                    if hp not in terms:
                        continue
                    names[disease] = row["disease_name"]
                    refs = annotations[hp].setdefault(disease, set())
                    refs.update(ref for ref in row.get("reference", "").split(";") if ref)
            self.terms, self.children = terms, children
            self.annotations, self.names, self.version = annotations, names, version
            self._loaded = True

    def search(self, body):
        if not isinstance(body, dict) or set(body) - {"region", "offset", "limit", "query"}:
            raise InputError("Use region, offset, limit and query only")
        region, offset, limit = body.get("region"), body.get("offset", 0), body.get("limit", 6)
        query = body.get("query", "")
        if not isinstance(region, str) or not re.fullmatch(r"HP:\d{7}", region):
            raise InputError("Use an anatomical HPO term")
        if type(offset) is not int or not 0 <= offset <= 100000 or type(limit) is not int or not 1 <= limit <= 12:
            raise InputError("Invalid atlas page")
        if not isinstance(query, str) or len(query) > 120:
            raise InputError("Use a short record filter")
        self._load()
        if region not in self.terms:
            raise InputError("Unknown anatomical HPO term")
        descendants, pending = set(), [region]
        while pending:
            hp = pending.pop()
            if hp in descendants:
                continue
            descendants.add(hp)
            pending.extend(self.children.get(hp, ()))
        matches = defaultdict(dict)
        for hp in sorted(descendants):
            for disease, refs in self.annotations.get(hp, {}).items():
                matches[disease][hp] = refs
        query = query.strip().casefold()
        ids = sorted((did for did in matches if not query or query in self.names[did].casefold() or query in did.casefold()),
                     key=lambda did: (self.names[did].casefold(), did))
        selected = ids[offset:offset + limit]
        nodes = {region: self.terms[region]}
        edges = []
        for did in selected:
            prefix, code = did.split(":", 1)
            url = ("https://omim.org/entry/" + code if prefix == "OMIM" else
                   "https://www.orpha.net/en/disease/detail/" + code if prefix == "ORPHA" else
                   "https://hpo.jax.org/browse/disease/" + did)
            nodes[did] = {"id": did, "label": self.names[did], "kind": "disease", "url": url,
                          "description": f"{len(matches[did])} recorded phenotypes in this anatomical region.", "providers": ["HPO"]}
            for hp, refs in matches[did].items():
                if hp not in nodes:
                    nodes[hp] = self.terms[hp]
                    edges.append({"from": region, "to": hp, "relation": "has_descendant", "source": "HPO", "evidence": []})
                edges.append({"from": did, "to": hp, "relation": "has_phenotype", "source": "HPO", "evidence": sorted(refs)})
                for ref in sorted(refs):
                    if not re.fullmatch(r"PMID:\d+", ref):
                        continue
                    nodes[ref] = {"id": ref, "label": ref, "kind": "paper", "url": "https://pubmed.ncbi.nlm.nih.gov/" + ref[5:] + "/",
                                  "description": "Publication cited by the HPO disease–phenotype annotation. Open PubMed for the title and full record.", "providers": ["HPO"]}
                    edges.append({"from": did, "to": ref, "relation": "annotation_cites", "source": "HPO", "evidence": [ref]})
        unique = {(e["from"], e["to"], e["relation"]): e for e in edges}
        return {"status": "ok" if selected else "empty", "root": region, "nodes": list(nodes.values()), "edges": list(unique.values()),
                "total": len(ids), "offset": offset, "nextOffset": offset + limit if offset + limit < len(ids) else None,
                "datasetVersion": self.version, "retrievedAt": datetime.now(timezone.utc).isoformat(),
                "unavailableProviders": [], "notice": "Regional associations from HPO annotations; records may describe the same condition under different identifiers."}
