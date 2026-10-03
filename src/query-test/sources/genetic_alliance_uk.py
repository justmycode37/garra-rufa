"""Genetic Alliance UK (https://geneticalliance.org.uk): its member patient organisations.

The WordPress REST API needs a login, but the A-Z member directory is a plain page
(robots.txt only disallows /wp-admin/):
  /membership/a-z-members-directory/   ~250 members as <li><a href=website>Name</a></li>
downloaded once into data/genetic_alliance_uk/members.json (delete to refresh).

The directory lists no diseases, so organisations are matched by name: every distinctive
word of the organisation name (minus words like "UK", "trust", "support", "group" and
generic medical words) must occur in one of the disease's names ("Alström Syndrome UK"
-> Alström syndrome, "Action Duchenne" -> Duchenne muscular dystrophy, "Ataxia UK" ->
Friedreich ataxia). This is conservative but heuristic, so the relation is
"patient_organisation_by_name". Organisation nodes are GAUK:<slug> with a WEB:<domain>
xref.

Node handling: disease -> matching member organisations, most specific name first.
"""
import html
import json
import re

from . import _groups
from .base import Edge, Node, Source, words

URL = "https://geneticalliance.org.uk/membership/a-z-members-directory/"
_ITEM = re.compile(r'<li><a href="([^"]+)"[^>]*>(.*?)</a></li>', re.S)
_SECTION = re.compile(r'<h5 id="[A-Z#]-list">')
# words that say nothing about which disease an organisation is about
ORG_STOP = {
    "uk", "gb", "britain", "british", "england", "scotland", "scottish", "wales", "welsh",
    "ireland", "irish", "national", "international", "europe", "european", "trust",
    "charity", "foundation", "society", "association", "alliance", "action", "support",
    "group", "network", "fund", "research", "information", "awareness", "campaign",
    "community", "families", "family", "parents", "children", "childrens", "child",
    "friends", "life", "living", "hope", "cure", "care", "help", "self", "beat", "fight",
    "team", "project", "rare", "genetic", "conditions", "disorders", "diseases",
    "syndromes", "medical", "health", "patients", "patient", "people", "advocacy",
    "fellowship", "positive", "the", "and",
    "prev", "formerly", "previously", "inc", "ltd", "cic", "cio", "org", "on", "for", "at",
    "cancer", "heart", "kidney", "lung", "liver", "eye", "skin", "brain", "blood", "bone",
    "chromosome", "disorder", "condition",
}


class GeneticAllianceUkSource(Source):
    name = "genetic_alliance_uk"
    id_prefixes = frozenset({"ORPHA", "ORPHANET", "MONDO", "OMIM", "GARD", "DOID", "NORD"})
    by_name = True

    def __init__(self):
        super().__init__()
        self._file = _groups.CachedFile("genetic_alliance_uk", "members.json", self._download)
        self._members: list[dict] | None = None

    def accepts(self, node: Node) -> bool:
        return super().accepts(node) and _groups.is_disease(node)

    def _download(self) -> str:
        r = self.session.get(URL, timeout=60)
        r.raise_for_status()
        page = r.text
        m = _SECTION.search(page)
        members = []
        for href, name in _ITEM.findall(page[m.start():] if m else page):
            name = html.unescape(re.sub(r"<[^>]+>", "", name)).strip()
            if name and href.startswith("http") and "geneticalliance.org.uk" not in href:
                members.append({"name": name, "website": href})
        if len(members) < 50:
            raise ValueError("member list not found (layout changed?)")
        return json.dumps(members, ensure_ascii=False)

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            return self._query(node, limit) if _groups.is_disease(node) else []
        except Exception:
            return []

    @staticmethod
    def _keys(name: str) -> list[set[str]]:
        """Word sets that identify the organisation's disease: the name without its
        parentheses and the text inside each ("Alex TLC (The Leukodystrophy Charity)")."""
        name = re.sub(r"\((?:prev|formerly|previously)[^)]*\)", "", name, flags=re.I)
        parts = [re.sub(r"\([^)]*\)", " ", name)] + re.findall(r"\(([^)]*)\)", name)
        keys = []
        for p in parts:
            k = {w for w in words(_groups.norm(p)) if w not in ORG_STOP and len(w) > 2}
            if k:
                keys.append(k)
        return keys

    @staticmethod
    def _covered(key: set[str], disease: set[str]) -> bool:
        # "gilberts" (Gilbert's) / "downs" also match "gilbert" / "down"
        return all(w in disease or (w.endswith("s") and w[:-1] in disease) for w in key)

    def _query(self, node: Node, limit: int) -> list[Edge]:
        if self._members is None:
            self._members = [dict(m, keys=self._keys(m["name"])) for m in self._file.json()]
        names = [words(n) for n in _groups.disease_names(node, self.session)]
        hits = [(max(len(k) for k in m["keys"] if any(self._covered(k, n) for n in names)), m)
                for m in self._members
                if any(self._covered(k, n) for k in m["keys"] for n in names)]
        hits.sort(key=lambda h: (-h[0], h[1]["name"]))
        return [Edge(node, _groups.org_node(m["name"], f"GAUK:{_groups.slug(m['name'])}",
                                            "patient_organisation", self.name, m["website"]),
                     "patient_organisation_by_name", self.name) for _, m in hits[:limit]]
