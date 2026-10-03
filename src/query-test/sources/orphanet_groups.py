"""Orphanet expert resources (https://www.orpha.net): who works on a disease.

The Orphadata API (orphadata.py) only has nomenclature, genes, phenotypes, epidemiology
and classifications; the expert-resource data (patient organisations, expert centres,
networks, research projects, registries, trials, biobanks) is only in the free Orphadata
products behind a data-access agreement. orpha.net shows it per disease on server-rendered
search pages, which this module reads (robots.txt only blocks named crawlers):

  /en/patient-organisations?orphaCode=558&diseaseName=<any>
  /en/patient-organisations/federations-alliances?orphaCode=558&diseaseName=<any>
  /en/expert-centres/centres/558?name=<any>&consulting=medical&age=all&official=0
  /en/expert-centres/networks?orphaCode=558&diseaseName=<any>      (incl. the ERNs)
  /en/institutions/expert-networks?orphaCode=558&diseaseName=<any>
  /en/research-trials/research-projects?orphaCode=558&mode=orpha&name=558
  /en/research-trials/registries?orphaCode=558&diseaseName=<any>
  /en/research-trials/clinical-trials?orphaCode=558&diseaseName=<any>
  /en/research-trials/biobanks?orphaCode=558&diseaseName=<any>

Each page lists result cards in up to three sections: specific to the disease, for its
subtypes ("particular forms") and for broader groups that include it. The relation says
which: "patient_organisation", "patient_organisation_for_subtype",
"patient_organisation_for_group". Specific results come first. Cards carry the
Orphanet resource id ("More information" link), local + English name and country.

ERN entries of the networks page are left to ern.py (one ERN:<code> node per network).
Node handling: ORPHA:* disease -> resources; anything else -> nothing.
"""
import html
import re
import threading

from . import _groups
from .base import Edge, Node, Source

BASE = "https://www.orpha.net/en/"
THROTTLE = _groups.Throttle(0.5)

# category -> (url path, extra params, relation); order = priority for small limits
CATEGORIES = {
    "patient": ("patient-organisations", {}, "patient_organisation"),
    "centre": ("expert-centres/centres/{code}",
               {"name": "disease", "consulting": "medical", "age": "all", "official": 0},
               "expert_centre"),
    "project": ("research-trials/research-projects", {"mode": "orpha", "name": "{code}"},
                "research_project"),
    "registry": ("research-trials/registries", {}, "patient_registry"),
    "expert_network": ("institutions/expert-networks", {}, "expert_network"),
    "network": ("expert-centres/networks", {}, "expert_network"),
    "trial": ("research-trials/clinical-trials", {}, "clinical_trial"),
    "federation": ("patient-organisations/federations-alliances", {},
                   "patient_organisation"),
    "biobank": ("research-trials/biobanks", {}, "biobank"),
}
# href path of a card -> (CURIE prefix, node kind)
ITEM = {
    "patient-organisations/patient": ("ORPHANET.PO", "patient_organisation"),
    "patient-organisations/federations-alliances": ("ORPHANET.PO", "patient_organisation"),
    "expert-centres/centre": ("ORPHANET.EC", "expert_centre"),
    "expert-centres/network": ("ORPHANET.NET", "expert_network"),
    "institutions/expert-networks": ("ORPHANET.NET", "expert_network"),
    "research-trials/research-projects": ("ORPHANET.RP", "research_project"),
    "research-trials/research-projects-network": ("ORPHANET.RP", "research_project"),
    "research-trials/registry": ("ORPHANET.REG", "registry"),
    "research-trials/clinical-trial": ("ORPHANET.CT", "clinical_trial"),
    "research-trials/clinical-trial-network": ("ORPHANET.CT", "clinical_trial"),
    "research-trials/biobank": ("ORPHANET.BB", "biobank"),
    "research-trials/biobank_network": ("ORPHANET.BB", "biobank"),
}
SECTION_SUFFIX = {"direct": "", "children": "_for_subtype", "parent": "_for_group"}

_SECTION = re.compile(r'id="(direct|children|parent)-relation"')
_CARD = re.compile(r'class="result-card')
_LINK = re.compile(r'<a href="/en/([a-z_-]+/[a-z_-]+)/(\d+)[^"]*"[^>]*>(.*?)</a>', re.S)
_COUNTRY = re.compile(r'<div class="fw-bold">\s*([^<]+?)\s*</div>')
_TAG = re.compile(r"<[^>]+>")

_pages: dict[str, str | None] = {}
_pages_lock = threading.Lock()


def fetch_page(session, category: str, code: str, name: str = "") -> str | None:
    """A category page for ORPHA:<code> (cached for the run; shared with ern.py). Some
    pages return nothing without a diseaseName; its value does not filter anything."""
    path, extra, _ = CATEGORIES[category]
    key = f"{category}:{code}"
    with _pages_lock:
        if key in _pages:
            return _pages[key]
    params = {"orphaCode": code, "diseaseName": name.strip() or "disease"}
    params.update({k: str(v).replace("{code}", code) for k, v in extra.items()})
    THROTTLE.wait()
    text = None
    try:
        r = session.get(BASE + path.replace("{code}", code), params=params, timeout=60)
        if r.status_code == 200:
            text = r.content.decode("utf-8", errors="replace")
    except Exception:
        text = None
    with _pages_lock:
        _pages[key] = text
    return text


def parse_cards(page: str) -> list[dict]:
    """Result cards in page order: section, href type, id, names, country, ERN flag."""
    marks = [(m.start(), m.group(1)) for m in _SECTION.finditer(page)]
    starts = [m.start() for m in _CARD.finditer(page)]
    cards = []
    for i, s in enumerate(starts):
        chunk = page[s: starts[i + 1] if i + 1 < len(starts) else len(page)]
        section = next((name for pos, name in reversed(marks) if pos < s), "direct")
        names, kind, rid = [], None, None
        for path, num, inner in _LINK.findall(chunk):
            text = html.unescape(_TAG.sub("", inner)).strip()
            kind, rid = kind or path, rid or num
            if text and text != "More information" and text not in names:
                names.append(text)
        if not rid or not names:
            continue
        c = _COUNTRY.search(chunk)
        cards.append({"section": section, "path": kind, "id": rid, "names": names,
                      "country": c.group(1).strip().title() if c else None,
                      "ern": "ERN.png" in chunk})
    return cards


def is_ern_card(card: dict) -> bool:
    """A card for a whole ERN (not one of its member centres or national networks)."""
    return (card["ern"] and card["path"] == "expert-centres/network"
            and any("reference network" in n.lower() for n in card["names"])
            and _groups.ern_code(" ".join(card["names"])) is not None)


class OrphanetGroupsSource(Source):
    name = "orphanet_groups"
    id_prefixes = frozenset({"ORPHA", "ORPHANET"})
    by_name = False

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            if not _groups.is_disease(node):
                return []
            codes = _groups.orpha_codes(node)
            return self._query(node, codes[0], limit) if codes else []
        except Exception:
            return []

    def _node(self, card: dict) -> Node:
        prefix, kind = ITEM.get(card["path"], ("ORPHANET.RES", "organisation"))
        # English name is the second link when the local name differs
        label = card["names"][1] if len(card["names"]) > 1 else card["names"][0]
        if card["country"]:
            label = f"{label} ({card['country']})"
        return Node(label, f"{prefix}:{card['id']}", kind, self.name,
                    info={"url": f"{BASE}{card['path']}/{card['id']}"})

    def _query(self, node: Node, code: str, limit: int) -> list[Edge]:
        # small limits (non-focus diseases) only look at the most useful categories
        cats = list(CATEGORIES) if limit >= 10 else list(CATEGORIES)[:max(3, limit)]
        specific: list[list[Edge]] = []
        broader: list[list[Edge]] = []
        for cat in cats:
            page = fetch_page(self.session, cat, code, node.label)
            if not page:
                continue
            rel = CATEGORIES[cat][2]
            spec, broad, seen = [], [], set()
            for card in parse_cards(page):
                if is_ern_card(card) or (card["path"], card["id"]) in seen:
                    continue
                seen.add((card["path"], card["id"]))
                e = Edge(node, self._node(card), rel + SECTION_SUFFIX[card["section"]],
                         self.name)
                (broad if card["section"] == "parent" else spec).append(e)
            specific.append(spec)
            broader.append(broad)
        out = _groups.interleave(specific, limit)
        return out + _groups.interleave(broader, limit - len(out))
