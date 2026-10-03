"""Deduplication: merge nodes from different sources that are the same entity.

Two nodes are the same entity when they share an identifier: their ids, their xrefs
with an IDENTITY prefix, the two ends of an exact "xref" edge, or (for genes) the gene
symbol. Identifiers are normalised first (ORPHANET:/Orphanet: -> ORPHA:, MONDO:17310 ->
MONDO:0017310, ...). Merging is union-find over these identifiers.

Xrefs with other prefixes (ICD, MeSH, UMLS, ...) are kept for display but never merge
anything: those terminologies are coarser, so one code can cover several diseases.
A merge is refused when it would put two different ids of a UNIQUE prefix into one
entity (e.g. two MONDO ids), which stops chains of loose mappings from collapsing
related-but-distinct diseases (Marfan syndrome vs. neonatal Marfan syndrome).
"""
import re

from sources import Node

PREFIXES = {"ORPHANET": "ORPHA", "ORPHA": "ORPHA", "MIM": "OMIM", "OMIM": "OMIM",
            "MONDO": "MONDO", "HP": "HP", "HGNC": "HGNC", "NCBIGENE": "NCBIGene",
            "DOID": "DOID", "NORD": "NORD", "GARD": "GARD", "DRUGBANK": "DrugBank",
            "SYMBOL": "SYMBOL", "ENSEMBL": "ENSEMBL", "CHEMBL": "ChEMBL", "EFO": "EFO",
            "UBERON": "UBERON", "FMA": "FMA", "CL": "CL", "GO": "GO", "CLINVAR": "ClinVar",
            # groups (sources/_groups.py): ERN:<code>, trials, organisation websites
            # (WEB:<domain> is what the same patient organisation shares across sites)
            "ERN": "ERN", "NCT": "NCT", "WEB": "WEB"}
PAD = {"MONDO": 7, "GARD": 7, "HP": 7, "UBERON": 7, "EFO": 7, "CL": 7, "GO": 7}
IDENTITY = set(PREFIXES.values())
# one Orphanet/OMIM entry often maps to a MONDO term together with its subtypes
UNIQUE = IDENTITY - {"OMIM", "ORPHA"}
# display id preference: the first prefix present in an entity names it
PRIORITY = ["MONDO", "HP", "HGNC", "NCBIGene", "ENSEMBL", "DrugBank", "ChEMBL", "ORPHA", "DOID",
            "OMIM", "UBERON", "FMA", "CL", "GO", "EFO", "ClinVar", "NORD", "GARD", "SYMBOL"]
CURIE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*:\S+$")


def normalize(curie: str) -> str:
    prefix, sep, local = curie.partition(":")
    if not sep:
        return curie
    prefix = PREFIXES.get(prefix.upper(), prefix)
    if prefix in PAD and local.isdigit():
        local = local.zfill(PAD[prefix])
    return f"{prefix}:{local}"


def _prefix(curie: str) -> str:
    return curie.split(":", 1)[0]


def _is_placeholder(node: Node) -> bool:
    """Nodes built from a bare xref carry their CURIE as label (e.g. "OMIM:154700")."""
    return bool(CURIE.match(node.label))


class Entities:
    def __init__(self):
        self._parent: dict[str, str] = {}
        self._ent: dict[str, dict] = {}  # root -> entity record

    # -- union-find ------------------------------------------------------
    def find(self, key: str) -> str:
        key = normalize(key)
        while self._parent[key] != key:
            self._parent[key] = self._parent[self._parent[key]]
            key = self._parent[key]
        return key

    def _new(self, key: str) -> str:
        self._parent[key] = key
        self._ent[key] = {"ids": {key}, "xrefs": set(), "label": None, "kind": "unknown",
                          "real": False, "sources": set(), "queried": set(), "id_labels": {}}
        return key

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        ea, eb = self._ent[ra], self._ent[rb]
        for p in UNIQUE:
            ia = {i for i in ea["ids"] if _prefix(i) == p}
            ib = {i for i in eb["ids"] if _prefix(i) == p}
            if ia and ib and ia != ib:
                return False
        if len(eb["ids"]) > len(ea["ids"]):
            ra, rb, ea, eb = rb, ra, eb, ea
        self._parent[rb] = ra
        ea["ids"] |= eb["ids"]
        ea["xrefs"] |= eb["xrefs"]
        ea["sources"] |= eb["sources"]
        ea["queried"] |= eb["queried"]
        for i, lab in eb["id_labels"].items():
            ea["id_labels"].setdefault(i, lab)
        if eb["real"] and not ea["real"]:
            ea.update(label=eb["label"], kind=eb["kind"], real=True)
        elif ea["kind"] in ("unknown", "term"):
            ea["kind"] = eb["kind"]
        del self._ent[rb]
        return True

    def merge_xref(self, a: str, b: str) -> bool:
        """Merge the two ends of an exact xref edge, if both are IDENTITY ids."""
        if _prefix(normalize(a)) in IDENTITY and _prefix(normalize(b)) in IDENTITY:
            return self.union(a, b)
        return False

    # -- adding nodes ----------------------------------------------------
    def _identity_keys(self, node: Node) -> list[str]:
        if node.id is None:
            return [node.key()]  # free-text term: only equal to itself
        keys = [normalize(node.id)]
        keys += [x for x in map(normalize, node.xrefs) if _prefix(x) in IDENTITY]
        if node.kind == "gene" and not CURIE.match(node.label):
            keys.append(f"SYMBOL:{node.label.upper()}")  # HGNC symbols are unique ids
        return list(dict.fromkeys(keys))

    def add(self, node: Node) -> str:
        """Register a node, merging it into any entity it shares an identifier with."""
        keys = self._identity_keys(node)
        for k in keys:
            if k not in self._parent:
                self._new(k)
        for k in keys[1:]:
            self.union(keys[0], k)  # refused merges just leave that key separate
        e = self._ent[self.find(keys[0])]
        e["xrefs"] |= {normalize(x) for x in node.xrefs if ":" in x}
        e["sources"].add(node.source)
        real = not _is_placeholder(node)
        if real and node.id:
            e["id_labels"].setdefault(normalize(node.id), node.label)
        if e["label"] is None or (real and not e["real"]):
            e.update(label=node.label, kind=node.kind, real=real)
        elif e["kind"] in ("unknown", "term"):
            e["kind"] = node.kind
        return self.find(keys[0])

    # -- views -----------------------------------------------------------
    def key(self, root: str) -> str:
        """Display id of an entity: its id with the highest-priority prefix."""
        ids = self._ent[self.find(root)]["ids"]
        rank = {p: i for i, p in enumerate(PRIORITY)}
        return min(ids, key=lambda i: (rank.get(_prefix(i), len(PRIORITY)), i))

    def record(self, root: str) -> dict:
        return self._ent[self.find(root)]

    def node(self, root: str) -> Node:
        """The merged entity as a Node, ready to hand to a source."""
        e = self.record(root)
        key = self.key(root)
        if key.startswith("term:"):
            return Node(e["label"], kind=e["kind"])
        rank = {p: i for i, p in enumerate(PRIORITY)}
        # several ids of one prefix (MONDO maps Marfan syndrome to ORPHA:558 and to its
        # subtype ORPHA:284963): the id whose own label is the entity's label comes first,
        # then ids seen with a real label, so sources look up the entity, not a subtype
        name = (e["label"] or "").lower()

        def order(i):
            lab = e["id_labels"].get(i)
            return (rank.get(_prefix(i), len(PRIORITY)),
                    0 if lab and lab.lower() == name else 1 if lab else 2, i)
        others = sorted(e["ids"] - {key}, key=order)
        xrefs = tuple(dict.fromkeys(others + sorted(e["xrefs"] - e["ids"])))
        return Node(e["label"], id=key, kind=e["kind"], source=min(e["sources"]), xrefs=xrefs)

    def mark_queried(self, root: str, source: str) -> bool:
        """True the first time `source` is asked about this entity, False afterwards."""
        q = self.record(root)["queried"]
        if source in q:
            return False
        q.add(source)
        return True

    def queried(self, root: str) -> set[str]:
        return self.record(root)["queried"]
