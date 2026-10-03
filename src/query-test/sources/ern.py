"""European Reference Networks (ERNs): the 24 EU networks of expert hospitals.

Two public sources, no registration:
  disease -> ERN   Orphanet's per-disease "networks of expert centres" page
                   (orphanet_groups.fetch_page, shared cache) lists the ERNs covering the
                   disease or a group containing it, marked with the ERN badge.
  ERN -> members   the EC "ERN Service Directory" backend
                   (https://webgate.ec.europa.eu/ernsd/backend/api/), the JSON API behind
                   https://webgate.ec.europa.eu/ernsd/cgi-bin/ern_public.cgi:
                     get_networks          24 networks: code, official name, URL, coordinator
                     get_clinicalcentres   ~1,600 member centres, each with its networkCode,
                                           hospital, country, centreType, isCoordinator
                   downloaded once into data/ern/ (delete to refresh).

ERN nodes are ERN:<networkCode> (see _groups.ERNS), so EURORDIS ePAG links land on the
same node. Member centres are ERN.HCP:<hcpId> ("healthcare_provider").

Node handling: ORPHA:* disease -> its ERNs ("reference_network"); ERN:* -> coordinating
centre(s) first, then full members spread over countries, then affiliated national
centres/hubs ("ern_coordinator" / "ern_member" / "ern_affiliated").
"""
import json

from . import _groups
from .base import Edge, Node, Source
from .orphanet_groups import fetch_page, is_ern_card, parse_cards

API = "https://webgate.ec.europa.eu/ernsd/backend/api/"
MEMBER_RELATION = {"Full Member": "ern_member", "Associated National Centre": "ern_affiliated",
                   "National Coordination Hub": "ern_affiliated"}


class ErnSource(Source):
    name = "ern"
    id_prefixes = frozenset({"ORPHA", "ORPHANET", "ERN"})
    by_name = False

    def __init__(self):
        super().__init__()
        self._networks = _groups.CachedFile("ern", "networks.json",
                                            lambda: self._download("get_networks"))
        self._centres = _groups.CachedFile("ern", "clinicalcentres.json",
                                           lambda: self._download("get_clinicalcentres"))

    def _download(self, endpoint: str) -> str:
        r = self.session.get(API + endpoint, timeout=120)
        r.raise_for_status()
        data = r.json()["data"]
        return json.dumps(data, ensure_ascii=False)

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            erns = [c.split(":", 1)[1] for c in self.ids_for(node) if c.upper().startswith("ERN:")]
            if erns:
                return self._members(node, erns[0], limit)
            codes = _groups.orpha_codes(node)
            if codes and _groups.is_disease(node):
                return self._by_disease(node, codes[0], limit)
            return []
        except Exception:
            return []

    def _ern(self, code: str) -> Node:
        net = next((n for n in self._networks.json() if n["networkCode"] == code), None)
        base = _groups.ern_node(code, self.name)
        web = _groups.web_xref(net.get("networkUrl")) if net else None
        return Node(base.label, base.id, base.kind, self.name, (web,) if web else ())

    def _by_disease(self, node: Node, code: str, limit: int) -> list[Edge]:
        page = fetch_page(self.session, "network", code, node.label)
        if not page:
            return []
        seen, edges = set(), []
        for card in parse_cards(page):
            if not is_ern_card(card):
                continue
            ern = _groups.ern_code(" ".join(card["names"]))
            if ern in seen:
                continue
            seen.add(ern)
            # Orphanet's own network id becomes an xref of the ERN node
            ern_n = self._ern(ern)
            ern_n = Node(ern_n.label, ern_n.id, ern_n.kind, self.name,
                         (*ern_n.xrefs, f"ORPHANET.NET:{card['id']}"))
            edges.append(Edge(node, ern_n, "reference_network", self.name))
        return edges[:limit]

    def _members(self, node: Node, code: str, limit: int) -> list[Edge]:
        rows = [c for c in self._centres.json()
                if c.get("networkCode", "").lower() == code.lower() and c.get("active") == 1]
        coord = [c for c in rows if c.get("isCoordinator") == 1]
        full = [c for c in rows if c not in coord and c.get("centreType") == "Full Member"]
        other = [c for c in rows if c not in coord and c not in full]

        def by_country(cs):  # round-robin over countries for a spread-out sample
            groups: dict[str, list] = {}
            for c in sorted(cs, key=lambda c: c.get("hcpOfficialNameEn") or ""):
                groups.setdefault(c.get("countryCode") or "", []).append(c)
            return _groups.interleave(list(groups.values()), len(cs))

        edges = []
        for c in coord + by_country(full) + by_country(other):
            rel = "ern_coordinator" if c in coord else MEMBER_RELATION.get(
                c.get("centreType"), "ern_member")
            edges.append(Edge(node, self._centre(c), rel, self.name))
            if len(edges) >= limit:
                break
        return edges

    def _centre(self, c: dict) -> Node:
        unit = (c.get("hcpOfficialNameEn") or c.get("hcpOfficialName") or "").strip()
        hosp = (c.get("mainHospitalEn") or c.get("mainHospital") or "").strip()
        label = unit if not hosp or hosp.lower() in unit.lower() else f"{unit}, {hosp}"
        if c.get("countryName"):
            label += f" ({c['countryName']})"
        return _groups.org_node(label, f"ERN.HCP:{c['hcpId']}", "healthcare_provider",
                                self.name, c.get("hcpUrl"))
