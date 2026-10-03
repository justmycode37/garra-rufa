"""Genetic Alliance (US, https://geneticalliance.org): Disease InfoSearch support groups.

Genetic Alliance's disease -> advocacy-organisation directory is Disease InfoSearch
(https://www.diseaseinfosearch.org, linked from /programs/disease-info-search). The Next.js
site reads it from its own JSON route (a Contentful export):
  /api/diseases?locale=en-US   disease entries: diseaseName, otherNames,
                               diseaseSupportGroups (organisation names)
(with locale=en the route answers HTTP 500). Downloaded once into
data/genetic_alliance_us/diseases.json (delete to refresh).

Coverage is small: the relaunched directory has ~30 diseases (the old 10,000-disease
version is gone), and support groups are names only (no website or id).
Diseases are matched by normalised name / other names (exact, see eurordis.py).
Organisation nodes are GAUS:<slug> ("patient_organisation").
"""
import json

from . import _groups
from .base import Edge, Node, Source

API = "https://www.diseaseinfosearch.org/api/diseases"


class GeneticAllianceUsSource(Source):
    name = "genetic_alliance_us"
    id_prefixes = frozenset({"ORPHA", "ORPHANET", "MONDO", "OMIM", "GARD", "DOID", "NORD"})
    by_name = True

    def __init__(self):
        super().__init__()
        self._file = _groups.CachedFile("genetic_alliance_us", "diseases.json", self._download)
        self._index: dict[str, list[dict]] | None = None

    def accepts(self, node: Node) -> bool:
        return super().accepts(node) and _groups.is_disease(node)

    def _download(self) -> str:
        r = self.session.get(API, params={"locale": "en-US"}, timeout=60)
        r.raise_for_status()
        items = [x["fields"] for x in r.json() if isinstance(x, dict) and "fields" in x]
        return json.dumps([{"name": f.get("diseaseName"), "other": f.get("otherNames") or [],
                            "groups": f.get("diseaseSupportGroups") or []} for f in items],
                          ensure_ascii=False)

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            return self._query(node, limit) if _groups.is_disease(node) else []
        except Exception:
            return []

    def _query(self, node: Node, limit: int) -> list[Edge]:
        if self._index is None:
            index: dict[str, list[dict]] = {}
            for d in self._file.json():
                for n in [d["name"], *d["other"]]:
                    for key in _groups.name_variants(n or ""):
                        index.setdefault(key, []).append(d)
            self._index = index
        groups: list[str] = []
        for n in _groups.disease_names(node, self.session):
            for d in self._index.get(n, []):
                groups += [g for g in d["groups"] if g not in groups]
        return [Edge(node, Node(g.strip(), f"GAUS:{_groups.slug(g)}", "patient_organisation",
                                self.name), "patient_organisation", self.name)
                for g in groups[:limit]]
