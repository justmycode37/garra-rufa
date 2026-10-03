"""EURORDIS - Rare Diseases Europe (https://www.eurordis.org): its member patient
organisations and the patient representatives (ePAGs) in the ERNs.

The member directory page is filled from two public JSON endpoints of the site's own
WordPress plugin (the wp/v2 post types only expose titles):
  /wp-json/eurordis/members   ~1,100 members: organisation, country, website, membership
                              (Full/Associate), federation/national-alliance flags and the
                              diseases they cover (Orphanet preferred terms)
  /wp-json/eurordis/epag      ~420 ePAG advocates: organisation, website, ERN
Both are downloaded once into data/eurordis/ (delete to refresh).

Diseases are matched by normalised name against the node label and the Orphanet
preferred terms of its ORPHA ids (_groups.disease_names). The site has no disease ids, so
this is a name lookup for id nodes too, but an exact one (equal normalised names, no
fuzzy/substring matching, which is what base.Source warns about). Organisation nodes are
EURORDIS:<slug> with a WEB:<domain> xref for the website.

Node handling: disease -> member organisations covering it, European federations first
("patient_organisation"); ERN:* -> organisations of that ERN's ePAG advocates
("epag_patient_organisation"). National alliances that list no diseases are skipped.
"""
import json

from . import _groups
from .base import Edge, Node, Source

API = "https://www.eurordis.org/wp-json/eurordis/"


class EurordisSource(Source):
    name = "eurordis"
    id_prefixes = frozenset({"ORPHA", "ORPHANET", "MONDO", "OMIM", "GARD", "ERN"})
    by_name = True

    def __init__(self):
        super().__init__()
        self._members_file = _groups.CachedFile("eurordis", "members.json",
                                                lambda: self._download("members"))
        self._epag_file = _groups.CachedFile("eurordis", "epag.json",
                                             lambda: self._download("epag"))
        self._index: dict[str, list[dict]] | None = None

    def _download(self, endpoint: str) -> str:
        r = self.session.get(API + endpoint, timeout=120)
        r.raise_for_status()
        return json.dumps(r.json(), ensure_ascii=False)

    def accepts(self, node: Node) -> bool:
        return super().accepts(node) and (node.kind == "ern" or _groups.is_disease(node))

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            if node.kind == "ern" or any(c.upper().startswith("ERN:") for c in self.ids_for(node)):
                return self._epag(node, limit)
            if _groups.is_disease(node):
                return self._by_disease(node, limit)
            return []
        except Exception:
            return []

    def _org(self, name: str, website: str | None, country: str | None) -> Node:
        label = f"{name} ({country})" if country else name
        return _groups.org_node(label, f"EURORDIS:{_groups.slug(name)}",
                                "patient_organisation", self.name, website)

    def _by_disease(self, node: Node, limit: int) -> list[Edge]:
        if self._index is None:
            index: dict[str, list[dict]] = {}
            for m in self._members_file.json():
                for d in m.get("diseases") or []:
                    for key in _groups.name_variants(d):
                        if m not in index.setdefault(key, []):
                            index[key].append(m)
            self._index = index
        hits: list[dict] = []
        for name in _groups.disease_names(node, self.session):
            hits += [m for m in self._index.get(name, []) if m not in hits]
        # European federations first, then the organisations most focused on the disease
        hits.sort(key=lambda m: (not m.get("federation"), len(m.get("diseases") or []),
                                 m.get("membership") != "Full", m["organisation"]))
        return [Edge(node, self._org(m["organisation"], m.get("country_website"),
                                     m.get("country_label")),
                     "patient_organisation", self.name) for m in hits[:limit]]

    def _epag(self, node: Node, limit: int) -> list[Edge]:
        code = next((c.split(":", 1)[1] for c in self.ids_for(node)
                     if c.upper().startswith("ERN:")), None)
        if not code:
            return []
        seen, edges = set(), []
        for p in self._epag_file.json():
            org = (p.get("organisation") or "").strip()
            if not org or _groups.ern_code(p.get("ern") or "") != code:
                continue
            if _groups.slug(org) in seen:
                continue
            seen.add(_groups.slug(org))
            edges.append(Edge(node, self._org(org, p.get("website"), None),
                              "epag_patient_organisation", self.name))
        return edges[:limit]
