"""NIH Rare Diseases Clinical Research Network (RDCRN, https://www.rarediseasesnetwork.org).

~20 NIH-funded research consortia, each studying a handful of related rare diseases with
patient advocacy groups (PAGs) as partners. No API; the Drupal site is server-rendered
and its robots.txt only disallows admin/search paths:
  /diseases                       all ~260 diseases studied, as links /diseases/<id>/<slug>
                                  (downloaded once into data/rdcrn/diseases.json)
  /diseases/<id>/<slug>           per disease: the consortium/consortia studying it (name,
                                  rdcrn.org/<acronym> link), their research studies grouped
                                  by recruiting status, and their partner PAGs (name, website)
                                  (cached in data/rdcrn/pages/<id>.html; prefetch them all
                                  with `python -m sources.rdcrn --prefetch` from src/query-test)

Matching: the disease list has names only, so diseases are matched by normalised name
(and the parts of "A / B (abbr)" names, _groups.name_variants) against the node label and the Orphanet preferred terms of its ORPHA ids (exact match,
see eurordis.py); a detail page is fetched only for a matched disease.

Nodes: consortium RDCRN:<acronym> ("research_consortium"), study RDCRN.STUDY:<protocol
number> ("research_study", label prefixed with its recruiting status), PAG
RDCRN.PAG:<slug> ("patient_organisation", WEB:<domain> xref).
Node handling: disease -> consortia ("research_consortium"), their studies
("research_study") and PAGs ("patient_organisation"), interleaved.
"""
import html
import json
import re
import sys
import threading
from urllib.parse import urljoin

from . import _groups
from .base import Edge, Node, Source

BASE = "https://www.rarediseasesnetwork.org"
THROTTLE = _groups.Throttle(0.5)
PAGES = _groups.DATA_ROOT / "rdcrn" / "pages"

_LIST_ITEM = re.compile(r'<li><a href="/diseases/(\d+)/([^"]+)">([^<]+)</a></li>')
_BLOCK = 'class="des-listing'
_CONSORTIUM = re.compile(r"<h3><b>(.*?)</b></h3>", re.S)
_ACRONYM = re.compile(r"\(([^()]+)\)\s*$")
_RDCRN_LINK = re.compile(r'href="https?://(?:www\.)?rdcrn\.org/([^"/]+)')
_STUDIES = re.compile(r'id="research-studies-\d+" role="tabpanel"')
_PAGS = re.compile(r'id="patient-org-\d+" role="tabpanel"')
_STUDY_OR_STATUS = re.compile(r'<h4 class="disease-acc-heading">\s*(.*?)\s*</h4>'
                              r'|<a href="([^"]+)" target="_blank">\s*<div>(.*?)</div>', re.S)
_PAG = re.compile(r'<a href="([^"]+)" target="_blank"><div>(.*?)</div></a>', re.S)
_TAG = re.compile(r"<[^>]+>")


def _text(s: str) -> str:
    return html.unescape(_TAG.sub("", s)).strip()


class RdcrnSource(Source):
    name = "rdcrn"
    id_prefixes = frozenset({"ORPHA", "ORPHANET", "MONDO", "OMIM", "GARD", "DOID", "NORD"})
    by_name = True

    def __init__(self):
        super().__init__()
        self._list = _groups.CachedFile("rdcrn", "diseases.json", self._download_list)
        self._index: dict[str, list[tuple[str, str]]] | None = None
        self._pages: dict[str, str | None] = {}
        self._lock = threading.Lock()

    def accepts(self, node: Node) -> bool:
        return super().accepts(node) and _groups.is_disease(node)

    def _download_list(self) -> str:
        r = self.session.get(f"{BASE}/diseases", timeout=60)
        r.raise_for_status()
        items = [{"id": i, "slug": s, "name": html.unescape(n).strip()}
                 for i, s, n in _LIST_ITEM.findall(r.text)]
        if not items:
            raise ValueError("no diseases on /diseases (layout changed?)")
        return json.dumps(items, ensure_ascii=False)

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            return self._query(node, limit) if _groups.is_disease(node) else []
        except Exception:
            return []

    def _matches(self, node: Node) -> list[tuple[str, str]]:
        with self._lock:
            if self._index is None:
                index: dict[str, list[tuple[str, str]]] = {}
                for d in self._list.json():
                    for key in _groups.name_variants(d["name"]):
                        index.setdefault(key, []).append((d["id"], d["slug"]))
                self._index = index
        hits: list[tuple[str, str]] = []
        for n in _groups.disease_names(node, self.session):
            hits += [h for h in self._index.get(n, []) if h not in hits]
        return hits

    def _page(self, did: str, slug: str) -> str | None:
        if did not in self._pages:
            path = PAGES / f"{did}.html"
            if path.exists():
                self._pages[did] = path.read_text(encoding="utf-8")
            else:
                THROTTLE.wait()
                r = self.session.get(f"{BASE}/diseases/{did}/{slug}", timeout=60)
                self._pages[did] = r.text if r.status_code == 200 else None
                if self._pages[did] is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(self._pages[did], encoding="utf-8")
        return self._pages[did]

    def prefetch(self) -> int:
        """Download every disease page not cached yet (about 260); returns the count.
        Uses urllib: the site omits its intermediate certificate, which the system trust
        store resolves but requests' certifi bundle does not."""
        import urllib.request
        n = 0
        PAGES.mkdir(parents=True, exist_ok=True)
        for d in self._list.json():
            path = PAGES / f"{d['id']}.html"
            if path.exists():
                continue
            THROTTLE.wait()
            req = urllib.request.Request(f"{BASE}/diseases/{d['id']}/{d['slug']}",
                                         headers={"User-Agent": self.session.headers[
                                             "User-Agent"]})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    path.write_text(r.read().decode("utf-8", "replace"), encoding="utf-8")
                n += 1
            except Exception as e:
                print(f"  {d['id']}: {e}", file=sys.stderr)
        return n

    def _query(self, node: Node, limit: int) -> list[Edge]:
        consortia, studies, pags = [], [], []
        seen: set[str] = set()
        for did, slug in self._matches(node):
            page = self._page(did, slug)
            for block in (page or "").split(_BLOCK)[1:]:
                c, ss, ps = self._parse(block)
                for lst, items in ((consortia, [c] if c else []), (studies, ss), (pags, ps)):
                    for n in items:
                        if n.id not in seen:
                            seen.add(n.id)
                            lst.append(n)
        groups = [[Edge(node, n, "research_consortium", self.name) for n in consortia],
                  [Edge(node, n, "research_study", self.name) for n in studies],
                  [Edge(node, n, "patient_organisation", self.name) for n in pags]]
        return _groups.interleave(groups, limit)

    def _parse(self, block: str) -> tuple[Node | None, list[Node], list[Node]]:
        m = _CONSORTIUM.search(block)
        consortium = None
        if m:
            name = _text(m.group(1))
            link = _RDCRN_LINK.search(block)
            acr = _ACRONYM.search(name)
            key = (link.group(1) if link else acr.group(1) if acr else _groups.slug(name))
            consortium = Node(name, f"RDCRN:{key.lower()}", "research_consortium", self.name,
                              (f"WEB:rdcrn.org/{key.lower()}",) if link else (),
                              {"url": f"https://www.rdcrn.org/{key.lower()}"} if link else {})
        s0, p0 = _STUDIES.search(block), _PAGS.search(block)
        studies = []
        if s0:
            status = None
            for st, href, title in _STUDY_OR_STATUS.findall(
                    block[s0.start(): p0.start() if p0 and p0.start() > s0.start() else len(block)]):
                if st:
                    status = _text(st)
                    continue
                title = _text(title)
                num = title.split(":", 1)[0].strip()
                sid = num if num.isdigit() else href.rstrip("/").rsplit("/", 1)[-1]
                label = f"{title} [{status}]" if status else title
                studies.append(Node(label, f"RDCRN.STUDY:{sid}", "research_study", self.name,
                                    info={"url": urljoin(BASE, href)} if href else {}))
        pags = []
        if p0:
            for href, name in _PAG.findall(block[p0.start():]):
                name = _text(name)
                if name:
                    pags.append(_groups.org_node(name, f"RDCRN.PAG:{_groups.slug(name)}",
                                                 "patient_organisation", self.name, href))
        return consortium, studies, pags


if __name__ == "__main__":
    if "--prefetch" in sys.argv:
        print(f"{RdcrnSource().prefetch()} pages downloaded into {PAGES}")
