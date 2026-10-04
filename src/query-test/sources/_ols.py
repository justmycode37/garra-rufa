"""Shared helpers for ontologies served by the EBI OLS4 API (no auth).

  /api/search?q=..&ontology=..&exact=true          exact label/synonym match
  /api/v2/ontologies/{ont}/classes/{iri}            class record: label, directParent,
                                                    relatedTo (every OWL restriction,
                                                    e.g. part_of / located_in / FMA's
                                                    regional_part), xrefs, linkedEntities
                                                    (labels for every IRI it mentions)
  .../classes/{iri}/children                       direct subclasses
  .../classes/{iri}/relatedFrom                    classes with a restriction pointing here

OlsOntologySource turns one class into edges: subclass_of / has_subclass, one relation
per OWL property in both directions (incoming ones get an inverse name), and xrefs.
Not a Source module itself (not listed in sources._MODULES).
"""
import re
from urllib.parse import quote

from .base import Edge, Node, Source
from .base import info as mkinfo
from .base import refs as mkrefs

OLS = "https://www.ebi.ac.uk/ols4/api"
OBO = "http://purl.obolibrary.org/obo/"
XREF_KEY = "http://www.geneontology.org/formats/oboInOwl#hasDbXref"

KIND_BY_PREFIX = {"UBERON": "anatomy", "FMA": "anatomy", "CL": "cell", "GO": "process",
                  "HP": "phenotype", "MONDO": "disease", "PR": "protein", "CHEBI": "chemical",
                  "NCBITAXON": "taxon", "PATO": "quality", "MESH": "term"}
# inverse names for properties seen from the other end (relatedFrom)
INVERSE = {"part_of": "has_part", "has_part": "part_of", "located_in": "location_of",
           "contains": "contained_in", "develops_from": "develops_into",
           "regional_part_of": "regional_part", "regional_part": "regional_part_of",
           "constitutional_part_of": "constitutional_part",
           "constitutional_part": "constitutional_part_of", "member_of": "has_member",
           "has_member": "member_of", "systemic_part_of": "systemic_part",
           "systemic_part": "systemic_part_of", "arterial_supply_of": "arterial_supply",
           "arterial_supply": "arterial_supply_of", "nerve_supply_of": "nerve_supply",
           "nerve_supply": "nerve_supply_of", "venous_drainage_of": "venous_drainage",
           "venous_drainage": "venous_drainage_of", "lymphatic_drainage_of": "lymphatic_drainage",
           "lymphatic_drainage": "lymphatic_drainage_of", "bounded_by": "bounds",
           "surrounded_by": "surrounds", "surrounds": "surrounded_by",
           "anterior_to": "posterior_to", "posterior_to": "anterior_to",
           "superior_to": "inferior_to", "inferior_to": "superior_to",
           "proximal_to": "distal_to", "distal_to": "proximal_to",
           "left_of": "right_of", "right_of": "left_of", "medial_to": "lateral_to",
           "lateral_to": "medial_to", "connected_to": "connected_to",
           "continuous_with": "continuous_with", "adjacent_to": "adjacent_to"}

_exact_cache: dict[tuple[str, str], tuple[str, str] | None] = {}


def enc(iri: str) -> str:
    return quote(quote(iri, safe=""), safe="")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def first(v):
    """OLS v2 values are str, list, or reification dicts {"value": ...}."""
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, dict):
        v = v.get("value")
    return v


def values(v) -> list[str]:
    if v is None:
        return []
    return [first(x) for x in (v if isinstance(v, list) else [v]) if first(x) is not None]


DEFINITION = "http://purl.obolibrary.org/obo/IAO_0000115"
EXACT_SYN = "http://www.geneontology.org/formats/oboInOwl#hasExactSynonym"
OBO_URL = "https://www.ebi.ac.uk/ols4/ontologies/{ont}/classes?iri={iri}"


def _def_refs(v) -> list[str]:
    """Citations of OLS v2 definitions: reification axioms carry hasDbXref (PMID:, DOI:,
    URLs; ORCIDs and Wikipedia are not papers and dropped by paper_ref)."""
    out = []
    for x in v if isinstance(v, list) else [v]:
        if isinstance(x, dict):
            for ax in x.get("axioms") or []:
                out += values(ax.get(XREF_KEY))
    return [r for r in mkrefs(out) if "orcid.org" not in r]


def v2_info(d: dict, ontology: str) -> dict:
    """Node.info from an OLS v2 class record: definition, exact synonyms, citations, url."""
    defs = d.get(DEFINITION) or d.get("definition")
    return mkinfo(description=first(defs if not isinstance(defs, list) else
                                     [x for x in defs if isinstance(x, dict)] or defs),
                  synonyms=values(d.get(EXACT_SYN)), refs=_def_refs(defs),
                  url=OBO_URL.format(ont=ontology, iri=quote(d.get("iri", ""), safe="")))


# search fields of a name search (mondo / disease_ontology "matches")
SEARCH_FIELDS = "obo_id,label,exact_synonyms"


def search_info(doc: dict) -> dict:
    """Node.info of an OLS search hit: its exact synonyms, so a name search for "Pompe
    disease" can see that "glycogen storage disease II" is that disease
    (improvements: name_focus)."""
    import improvements
    syn = doc.get("exact_synonyms") or []
    return mkinfo(synonyms=list(syn)) if syn and improvements.on("name_focus") else {}


def v1_info(term: dict, ontology: str) -> dict:
    """Node.info from an OLS v1 term record (mondo / disease_ontology): the definition,
    exact synonyms (abbreviations separately: "MFS" is useless as a search term), the
    definition citations and "seeAlso"/curated-resource links as refs, the OLS page."""
    syn, abbr = [], []
    for x in term.get("obo_synonym") or []:
        if x.get("scope") == "hasExactSynonym":
            (abbr if x.get("type") == "abbreviation" else syn).append(x["name"])
    if not term.get("obo_synonym"):
        syn = list(term.get("synonyms") or [])
    defs = term.get("obo_definition_citation") or []
    cites = [x.get("url") or x.get("id") for c in defs for x in c.get("oboXrefs") or []]
    cites += [f"{x['database']}:{x['id']}" for c in defs for x in c.get("oboXrefs") or []
              if x.get("database") and x.get("id")]
    ann = term.get("annotation") or {}
    links = [*ann.get("seeAlso", []), *ann.get("curated content resource", []), *cites]
    # "description" also holds comments ("OMIM mapping confirmed by DO."): prefer the
    # obo definition
    desc = next((c["definition"] for c in defs if c.get("definition")), None)
    return mkinfo(description=desc or first(term.get("description")), synonyms=syn,
                  abbreviations=abbr, refs=mkrefs(cites),
                  links=list(dict.fromkeys(u for u in links if "://" in str(u))),
                  url=OBO_URL.format(ont=ontology, iri=quote(term.get("iri", ""), safe="")))


def exact_match(session, label: str, ontology: str) -> tuple[str, str] | None:
    """(CURIE, label) of the class whose label or synonym equals `label`, else None."""
    key = (ontology, label.lower())
    if key not in _exact_cache:
        hit = None
        try:
            r = session.get(f"{OLS}/search", timeout=30, params={
                "q": label, "ontology": ontology, "exact": "true", "type": "class",
                "queryFields": "label,synonym", "fieldList": "obo_id,label", "rows": 1,
                "obsoletes": "false"})
            docs = r.json()["response"]["docs"] if r.ok else []
            if docs and docs[0].get("obo_id"):
                hit = (docs[0]["obo_id"], docs[0]["label"])
        except Exception:
            pass
        _exact_cache[key] = hit
    return _exact_cache[key]


def interleave(groups: list[list[Edge]], limit: int) -> list[Edge]:
    """Round-robin over relation groups so one big group cannot crowd out the rest;
    an edge to a target already taken (same relation) is skipped."""
    out: list[Edge] = []
    seen: set[tuple] = set()
    i = 0
    while len(out) < limit and any(i < len(g) for g in groups):
        for g in groups:
            if i < len(g) and len(out) < limit:
                k = (g[i].relation, g[i].dst.key())
                if k not in seen:
                    seen.add(k)
                    out.append(g[i])
        i += 1
    return out


class OlsOntologySource(Source):
    """One OLS ontology. Subclasses set name, id_prefixes, ONTOLOGY, PREFIX and may
    override iri()/curie()/xref_edges()."""
    ONTOLOGY = ""
    PREFIX = ""
    KIND = "anatomy"
    by_name = True
    PAGE = 200  # max related classes fetched per request

    # -- id mapping --------------------------------------------------------
    def iri(self, curie: str) -> str:
        return OBO + curie.replace(":", "_", 1)

    def curie(self, iri: str, ols_curie: str | None = None) -> str | None:
        if iri.startswith(OBO):
            return iri[len(OBO):].replace("_", ":", 1)
        return ols_curie if ols_curie and ":" in ols_curie else None

    def kind_of(self, curie: str) -> str:
        return KIND_BY_PREFIX.get(curie.split(":", 1)[0].upper(), "term")

    def xref_edges(self, src: Node, xrefs: list[str]) -> list[Edge]:
        return []

    # -- query -------------------------------------------------------------
    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            if node.id is None:
                return self._search(node)
            for c in self.ids_for(node):
                edges = self._relations(node, f"{self.PREFIX}:{c.split(':', 1)[1]}", limit)
                if edges:
                    return edges
            return []
        except Exception:
            return []

    def _search(self, node: Node) -> list[Edge]:
        # exact label/synonym only: a fuzzy anatomy search on e.g. a disease name
        # ("Marfan syndrome") would only produce unrelated body parts
        hit = exact_match(self.session, node.label.strip(), self.ONTOLOGY)
        if not hit:
            return []
        return [Edge(node, Node(hit[1], self._norm(hit[0]), self.KIND, self.name), "matches",
                     self.name)]

    def _norm(self, curie: str) -> str:
        """OLS search obo_id -> this source's CURIE."""
        return curie

    def _class_url(self, curie: str) -> str:
        return f"{OLS}/v2/ontologies/{self.ONTOLOGY}/classes/{enc(self.iri(curie))}"

    def _page(self, url: str) -> list[dict]:
        try:
            return self.get_json(url, params={"size": self.PAGE}).get("elements") or []
        except Exception:
            return []

    def _node(self, iri: str, linked: dict, ols_curie: str | None = None,
              label=None) -> Node | None:
        info = linked.get(iri) or {}
        cur = self.curie(iri, ols_curie or info.get("curie"))
        if not cur or cur.upper().startswith("NCBITAXON:"):  # taxon constraints: not anatomy
            return None
        return Node(first(label) or first(info.get("label")) or cur, cur, self.kind_of(cur),
                    self.name)

    def _relations(self, node: Node, curie: str, limit: int) -> list[Edge]:
        url = self._class_url(curie)
        d = self.get_json(url)
        iri, linked = d["iri"], d.get("linkedEntities") or {}
        xrefs = [x for x in values(d.get(XREF_KEY)) if ":" in x and " " not in x]
        src = Node(node.label if node.id else first(d.get("label")) or curie, curie,
                   self.KIND, node.source if node.id else self.name,
                   tuple(dict.fromkeys((*node.xrefs, *xrefs))), v2_info(d, self.ONTOLOGY))
        groups: list[list[Edge]] = []

        parents = [self._node(p, linked) for p in values(d.get("directParent"))]
        groups.append([Edge(src, p, "subclass_of", self.name) for p in parents if p])
        children = [self._node(c["iri"], {}, c.get("curie"), c.get("label"))
                    for c in self._page(f"{url}/children")]
        groups.append([Edge(src, c, "has_subclass", self.name) for c in children if c])

        by_rel: dict[str, list[Edge]] = {}
        for r in d.get("relatedTo") or []:
            prop = slug(first((linked.get(r["property"]) or {}).get("label"))
                        or r["property"].rsplit("/", 1)[-1])
            dst = self._node(r["value"], linked)
            if dst:
                by_rel.setdefault(prop, []).append(Edge(src, dst, prop, self.name))
        for el in self._page(f"{url}/relatedFrom"):
            el_linked = el.get("linkedEntities") or {}
            dst = self._node(el["iri"], {}, el.get("curie"), el.get("label"))
            if not dst:
                continue
            for r in el.get("relatedTo") or []:
                if r.get("value") != iri:
                    continue
                prop = slug(first((el_linked.get(r["property"]) or {}).get("label"))
                            or r["property"].rsplit("/", 1)[-1])
                rel = INVERSE.get(prop, f"inverse_{prop}")
                by_rel.setdefault(rel, []).append(Edge(src, dst, rel, self.name))
        groups += by_rel.values()
        # exact mappings first (all of them): they link this term to the other anatomy sources
        xr = self.xref_edges(src, xrefs)[:limit]
        return xr + interleave(groups, limit - len(xr))
