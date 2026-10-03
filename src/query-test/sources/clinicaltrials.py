"""ClinicalTrials.gov API v2 (https://clinicaltrials.gov/data-api/api, no key): trials of a
disease and the organisations behind them.

  GET /api/v2/studies?query.term=AREA[ConditionMeshId]D008382   by MeSH descriptor
  GET /api/v2/studies?query.cond="Marfan syndrome"              by condition phrase

A disease with a MeSH D-id xref is looked up by that id (ClinicalTrials.gov maps each
study's conditions to MeSH, so this is an exact id lookup). Without one, the quoted label
is used as a condition search; the registry has no other disease ids, so this is the one
place where an id node is searched by name (ClinicalTrials.gov adds its own synonyms).
Both searches go through ClinicalTrials.gov's automatic condition -> MeSH mapping, which
errs ("CCA" for cholangiocarcinoma became congenital contractural arachnodactyly), so a
study is kept only if its own conditions, keywords or title contain one of the disease's
names (label, Orphanet preferred terms, the MeSH heading).

Trials are ranked recruiting / active first, then by last update. Per trial: the trial
(NCT:<id>, "clinical_trial", label carries the status), its lead sponsor ("sponsored_by")
and collaborators ("collaborator"), and the affiliations of its overall officials
("investigator_affiliation"). Organisations are CTGOV.ORG:<slug> ("organisation"), so
a sponsor running several trials of the disease becomes a hub.
"""
from . import _groups
from .base import Edge, Node, Source

API = "https://clinicaltrials.gov/api/v2/studies"
FIELDS = ",".join([
    "protocolSection.identificationModule.nctId",
    "protocolSection.identificationModule.briefTitle",
    "protocolSection.statusModule.overallStatus",
    "protocolSection.statusModule.lastUpdatePostDateStruct",
    "protocolSection.sponsorCollaboratorsModule",
    "protocolSection.contactsLocationsModule.overallOfficials",
    "protocolSection.conditionsModule",
    "derivedSection.conditionBrowseModule.meshes",
])
STATUS_RANK = ["RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION",
               "ACTIVE_NOT_RECRUITING", "AVAILABLE", "COMPLETED"]
THROTTLE = _groups.Throttle(0.3)  # the API allows ~50 requests/minute


class ClinicalTrialsSource(Source):
    name = "clinicaltrials"
    id_prefixes = frozenset({"MESH", "MONDO", "ORPHA", "ORPHANET", "OMIM", "GARD", "DOID",
                             "NORD", "EFO"})
    by_name = True

    def accepts(self, node: Node) -> bool:
        return super().accepts(node) and _groups.is_disease(node)

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            return self._query(node, limit) if _groups.is_disease(node) else []
        except Exception:
            return []

    def _mesh(self, node: Node) -> str | None:
        return next((c.split(":", 1)[1] for c in (node.id, *node.xrefs)
                     if c and c.split(":", 1)[0].upper() == "MESH"
                     and c.split(":", 1)[1].startswith("D")), None)

    def _search(self, node: Node, size: int) -> list[dict]:
        mesh = self._mesh(node)
        params = {"pageSize": size, "fields": FIELDS, "sort": "LastUpdatePostDate:desc",
                  "format": "json"}
        if mesh:
            params["query.term"] = f"AREA[ConditionMeshId]{mesh}"
        else:
            label = node.label.replace('"', "").strip()
            if not label:
                return []
            params["query.cond"] = f'"{label}"'
        THROTTLE.wait()
        return self.get_json(API, params=params).get("studies") or []

    def _org(self, name: str) -> Node:
        return Node(name.strip(), f"CTGOV.ORG:{_groups.slug(name)}", "organisation", self.name)

    def _about(self, study: dict, names: list[str], mesh: str | None) -> bool:
        """Whether the sponsor's own description of the study names the disease."""
        meshes = (study.get("derivedSection") or {}).get("conditionBrowseModule", {})
        names = names + [_groups.norm(m.get("term", "")) for m in meshes.get("meshes") or []
                         if mesh and m.get("id") == mesh]
        p = study["protocolSection"]
        cm = p.get("conditionsModule") or {}
        texts = [*(cm.get("conditions") or []), *(cm.get("keywords") or []),
                 p["identificationModule"].get("briefTitle") or ""]
        texts = [f" {_groups.norm(x)} " for x in texts]
        return any(f" {n} " in t for n in names if n for t in texts)

    def _query(self, node: Node, limit: int) -> list[Edge]:
        names = _groups.disease_names(node, self.session)
        mesh = self._mesh(node)
        studies = [s for s in self._search(node, min(100, max(20, limit * 4)))
                   if self._about(s, names, mesh)]

        def rank(s):
            st = s["protocolSection"].get("statusModule", {}).get("overallStatus", "")
            return STATUS_RANK.index(st) if st in STATUS_RANK else len(STATUS_RANK)
        studies.sort(key=rank)  # stable: keeps last-update order within a status
        per_trial: list[list[Edge]] = []
        for s in studies:
            p = s["protocolSection"]
            nct = p["identificationModule"]["nctId"]
            status = p.get("statusModule", {}).get("overallStatus", "").replace("_", " ").lower()
            title = p["identificationModule"].get("briefTitle") or nct
            trial = Node(f"{title} [{status}]" if status else title, f"NCT:{nct}",
                         "clinical_trial", self.name)
            edges = [Edge(node, trial, "clinical_trial", self.name)]
            sc = p.get("sponsorCollaboratorsModule") or {}
            lead = (sc.get("leadSponsor") or {}).get("name")
            if lead:
                edges.append(Edge(trial, self._org(lead), "sponsored_by", self.name))
            for c in sc.get("collaborators") or []:
                if c.get("name"):
                    edges.append(Edge(trial, self._org(c["name"]), "collaborator", self.name))
            seen = {lead}
            for o in (p.get("contactsLocationsModule") or {}).get("overallOfficials") or []:
                aff = (o.get("affiliation") or "").strip()
                if aff and aff not in seen:
                    seen.add(aff)
                    edges.append(Edge(trial, self._org(aff), "investigator_affiliation",
                                      self.name))
            per_trial.append(edges)
        # each trial with its lead sponsor first, then the remaining organisations
        out = [e for t in per_trial for e in t[:2]][:limit]
        rest = [e for t in per_trial for e in t[2:]]
        return out + rest[:limit - len(out)]
