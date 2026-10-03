"""Human Phenotype Ontology (HPO) via the public JAX HPO API (no auth).

Base URL: https://ontology.jax.org/api
  /hp/search?q=...                 phenotype term search
  /hp/terms/{HP:id}/parents|children   ontology hierarchy
  /network/annotation/{id}         HP term -> annotated diseases + genes;
                                   OMIM/ORPHA disease -> phenotypes + genes;
                                   NCBIGene -> phenotypes + diseases
  /network/search/disease|gene?q=  disease / gene search (also maps MONDO/HGNC via label)
Diseases are returned as MONDO CURIEs when HPO knows the mapping (original
OMIM/ORPHA id kept in xrefs); genes use NCBIGene CURIEs.
"""
from .base import Edge, Node, Source

API = "https://ontology.jax.org/api"
HPO_DISEASE_PREFIXES = {"OMIM", "ORPHA", "DECIPHER"}


class HpoSource(Source):
    name = "hpo"

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            return self._query(node, limit)
        except Exception:
            return []

    def _query(self, node: Node, limit: int) -> list[Edge]:
        if node.id is None:
            return self._search(node, limit)
        for cand in (node.id, *node.xrefs):
            prefix = cand.split(":", 1)[0].upper()
            if prefix == "HP":
                return self._from_term(node, cand, limit)
            if prefix in HPO_DISEASE_PREFIXES:
                edges = self._from_disease(node, cand, limit)
                if edges:
                    return edges
            if prefix == "NCBIGENE":
                return self._from_gene(node, cand, limit)
        # MONDO / HGNC / unknown: resolve through label search
        return self._resolve_by_label(node, limit)

    # -- node builders ---------------------------------------------------
    def _phen(self, d: dict) -> Node:
        return Node(d.get("name") or d["id"], id=d["id"], kind="phenotype", source=self.name)

    def _dis(self, d: dict) -> Node:
        mondo = d.get("mondoId")
        xr = (d["id"],) if mondo else ()
        return Node(d.get("name") or d["id"], id=mondo or d["id"], kind="disease",
                    source=self.name, xrefs=xr)

    def _gene(self, d: dict) -> Node:
        return Node(d.get("name") or d["id"], id=d["id"], kind="gene", source=self.name)

    # -- query paths -----------------------------------------------------
    def _search(self, node: Node, limit: int) -> list[Edge]:
        q = node.label
        n_dis = limit // 3
        terms = self.get_json(f"{API}/hp/search", params={"q": q, "max": limit}).get("terms", [])
        edges = [Edge(node, Node(t["name"], id=t["id"], kind="phenotype", source=self.name,
                                 xrefs=tuple(t.get("xrefs") or ())), "matches", self.name)
                 for t in terms[:limit - n_dis]]
        dis = self.get_json(f"{API}/network/search/disease",
                            params={"q": q, "limit": max(n_dis, limit - len(edges))}).get("results", [])
        edges += [Edge(node, self._dis(d), "matches", self.name)
                  for d in dis[:max(n_dis, limit - len(edges))]]
        seen: set[str] = set()
        edges = [e for e in edges if not (e.dst.id in seen or seen.add(e.dst.id))]
        return edges[:limit]

    def _resolve_by_label(self, node: Node, limit: int) -> list[Edge]:
        kind = node.kind
        if kind in ("disease", "unknown", "term"):
            res = self.get_json(f"{API}/network/search/disease",
                                params={"q": node.label, "limit": 50}).get("results", [])
            for d in res:
                if d.get("mondoId") == node.id or d["id"] in node.xrefs:
                    return self._from_disease(node, d["id"], limit)
        if kind in ("gene", "unknown", "term"):
            res = self.get_json(f"{API}/network/search/gene",
                                params={"q": node.label, "limit": 5}).get("results", [])
            for g in res:
                if g["name"].lower() == node.label.lower():
                    return self._from_gene(node, g["id"], limit)
        return []

    def _from_term(self, node: Node, hp: str, limit: int) -> list[Edge]:
        src = Node(node.label, id=hp, kind="phenotype", source=node.source, xrefs=node.xrefs)
        edges: list[Edge] = []
        n = max(1, limit // 4)
        for rel, path in (("subclass_of", "parents"), ("has_subclass", "children")):
            for t in self.get_json(f"{API}/hp/terms/{hp}/{path}")[:n]:
                edges.append(Edge(src, self._phen(t), rel, self.name))
        ann = self.get_json(f"{API}/network/annotation/{hp}")
        for rel, key in (("phenotype_of", "diseases"), ("associated_gene", "genes")):
            maker = self._dis if key == "diseases" else self._gene
            room = limit - len(edges) if key == "diseases" else limit - len(edges)
            take = max(1, room // 2) if key == "diseases" else max(0, room)
            for d in (ann.get(key) or [])[:take]:
                edges.append(Edge(src, maker(d), rel, self.name))
        return edges[:limit]

    def _from_disease(self, node: Node, did: str, limit: int) -> list[Edge]:
        ann = self.get_json(f"{API}/network/annotation/{did}")
        info = ann.get("disease") or {}
        src = Node(node.label, id=node.id or info.get("mondoId") or did,
                   kind="disease", source=node.source,
                   xrefs=tuple(dict.fromkeys((*node.xrefs, did))))
        n_gene = max(1, limit // 4)
        edges = [Edge(src, self._gene(g), "gene_associated", self.name)
                 for g in (ann.get("genes") or [])[:n_gene]]
        # round-robin over body-system categories so the first one can't crowd out the rest
        cats = [c for c in (ann.get("categories") or {}).values()]
        seen: set[str] = set()
        i = 0
        while len(edges) < limit and any(i < len(c) for c in cats):
            for items in cats:
                if i < len(items) and len(edges) < limit and items[i]["id"] not in seen:
                    seen.add(items[i]["id"])
                    edges.append(Edge(src, self._phen(items[i]), "has_phenotype", self.name))
            i += 1
        return edges[:limit]

    def _from_gene(self, node: Node, gid: str, limit: int) -> list[Edge]:
        ann = self.get_json(f"{API}/network/annotation/{gid}")
        src = Node(node.label, id=node.id or gid, kind="gene", source=node.source,
                   xrefs=tuple(dict.fromkeys((*node.xrefs, gid))))
        dis = ann.get("diseases") or []
        phen = ann.get("phenotypes") or []
        n_dis = min(len(dis), max(1, limit // 3))
        edges = [Edge(src, self._dis(d), "gene_associated", self.name) for d in dis[:n_dis]]
        edges += [Edge(src, self._phen(p), "has_phenotype", self.name)
                  for p in phen[:limit - len(edges)]]
        return edges[:limit]
