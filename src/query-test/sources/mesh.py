"""MeSH Anatomy tree (tree A) via NLM's public MeSH RDF API (no auth).

  https://id.nlm.nih.gov/mesh/lookup/descriptor?label=..&match=exact   name lookup
  https://id.nlm.nih.gov/mesh/sparql                                    SPARQL over MeSH RDF

For a MESH:D* descriptor that sits in the Anatomy tree (a tree number starting with
"A") this yields the tree neighbours inside tree A (subclass_of = broader descriptor,
has_subclass = narrower descriptor) and MeSH "see also" links (any tree; e.g. Aorta ->
Aortography). Descriptors outside tree A (diseases, drugs, ... which MONDO/Orphanet
xref as MESH:*) return [] since only the anatomy tree is in scope. Free text: exact
descriptor name, only when it is an anatomy descriptor.

Extra data: every MESH:D* descriptor (any tree) gets its heading, tree numbers, scope note
and MeSH browser url as Node.info (mesh_heading is what PubMed's [MeSH Terms] search
needs). For a node that only carries a non-anatomy MeSH xref (a disease) this is the
one edge returned: node --mesh_heading--> the labelled MESH:D* node.
"""
from ._ols import interleave
from .base import Edge, Node, Source, info

BASE = "https://id.nlm.nih.gov/mesh"
# tree letter -> node kind (for "see also" targets outside anatomy)
TREE_KIND = {"A": "anatomy", "B": "organism", "C": "disease", "D": "drug", "E": "procedure",
             "F": "behavior", "G": "process", "H": "discipline", "I": "concept",
             "J": "technology", "K": "concept", "L": "concept", "M": "group", "N": "concept",
             "V": "publication", "Z": "location"}
QUERY = """PREFIX meshv: <http://id.nlm.nih.gov/mesh/vocab#>
PREFIX mesh: <http://id.nlm.nih.gov/mesh/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?rel ?d ?label ?tn WHERE {
  { BIND(mesh:%(id)s AS ?d) BIND("self" AS ?rel) }
  UNION { mesh:%(id)s meshv:broaderDescriptor ?d . BIND("subclass_of" AS ?rel) }
  UNION { ?d meshv:broaderDescriptor mesh:%(id)s . BIND("has_subclass" AS ?rel) }
  UNION { mesh:%(id)s meshv:seeAlso ?d . BIND("see_also" AS ?rel) }
  UNION { ?d meshv:seeAlso mesh:%(id)s . BIND("see_also" AS ?rel) }
  ?d rdfs:label ?label .
  OPTIONAL { ?d meshv:treeNumber ?t . BIND(STRAFTER(STR(?t), "mesh/") AS ?tn) }
}"""


NOTE_QUERY = """PREFIX meshv: <http://id.nlm.nih.gov/mesh/vocab#>
PREFIX mesh: <http://id.nlm.nih.gov/mesh/>
SELECT ?note WHERE { mesh:%(id)s meshv:preferredConcept ?c . ?c meshv:scopeNote ?note }"""
PAGE = "https://meshb.nlm.nih.gov/record/ui?ui={}"


class MeshSource(Source):
    name = "mesh"
    id_prefixes = frozenset({"MESH", "MSH"})
    by_name = True

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            if node.id is None:
                return self._search(node)
            for c in self.ids_for(node):
                did = c.split(":", 1)[1]
                if did.startswith("D") and did[1:].isdigit():
                    return self._relations(node, did, limit)
            return []
        except Exception:
            return []

    def _records(self, did: str) -> dict[tuple[str, str], dict]:
        """(relation, descriptor id) -> {label, trees} for the descriptor and its neighbours."""
        d = self.get_json(f"{BASE}/sparql", params={
            "query": QUERY % {"id": did}, "format": "JSON", "inference": "false",
            "limit": 1000})
        out: dict[tuple[str, str], dict] = {}
        for b in d["results"]["bindings"]:
            key = (b["rel"]["value"], b["d"]["value"].rsplit("/", 1)[1])
            rec = out.setdefault(key, {"label": b["label"]["value"], "trees": set()})
            if "tn" in b:
                rec["trees"].add(b["tn"]["value"])
        return out

    @staticmethod
    def _anatomy(rec: dict) -> bool:
        return any(t.startswith("A") for t in rec["trees"])

    def _search(self, node: Node) -> list[Edge]:
        hits = self.get_json(f"{BASE}/lookup/descriptor",
                             params={"label": node.label.strip(), "match": "exact", "limit": 3})
        for h in hits:
            did = h["resource"].rsplit("/", 1)[1]
            rec = self._records(did).get(("self", did))
            if rec and self._anatomy(rec):
                return [Edge(node, Node(rec["label"], f"MESH:{did}", "anatomy", self.name),
                             "matches", self.name)]
        return []

    def _info(self, did: str, rec: dict) -> dict:
        try:
            b = self.get_json(f"{BASE}/sparql", params={
                "query": NOTE_QUERY % {"id": did}, "format": "JSON", "inference": "false"})
            note = next((x["note"]["value"] for x in b["results"]["bindings"]), None)
        except Exception:
            note = None
        return info(description=note, mesh_heading=rec["label"], mesh_trees=sorted(rec["trees"]),
                    url=PAGE.format(did))

    def _relations(self, node: Node, did: str, limit: int) -> list[Edge]:
        recs = self._records(did)
        me = recs.get(("self", did))
        if not me:
            return []
        if not self._anatomy(me):
            if node.id == f"MESH:{did}":
                return []
            kind = TREE_KIND.get(min(me["trees"] or {"?"})[0], "term")
            dst = Node(me["label"], f"MESH:{did}", kind, self.name, info=self._info(did, me))
            src = Node(node.label, node.id, node.kind, node.source, node.xrefs,
                       info(mesh_heading=me["label"]))
            return [Edge(src, dst, "mesh_heading", self.name)]
        node = Node(node.label, node.id, node.kind, node.source, node.xrefs,
                    self._info(did, me))
        groups: dict[str, list[Edge]] = {}
        for (rel, other), rec in sorted(recs.items(), key=lambda kv: min(kv[1]["trees"] or {""})):
            if rel == "self" or other == did:
                continue
            if rel != "see_also" and not self._anatomy(rec):
                continue  # broader/narrower descriptors in other trees (e.g. C for diseases)
            kind = TREE_KIND.get(min(rec["trees"] or {"?"})[0], "term")
            dst = Node(rec["label"], f"MESH:{other}", kind, self.name)
            groups.setdefault(rel, []).append(Edge(node, dst, rel, self.name))
        return interleave(list(groups.values()), limit)
