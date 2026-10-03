"""NORD (National Organization for Rare Disorders, https://rarediseases.org).

NORD has no documented public API, but its WordPress site exposes the standard
read-only JSON REST endpoint (not disallowed in robots.txt):
  https://rarediseases.org/wp-json/wp/v2/rare-diseases?search=...   (list/search)
  https://rarediseases.org/wp-json/wp/v2/rare-diseases/{id}         (by NORD id)
The post id equals the NORD identifier (Marfan syndrome = NORD:1403, which is also
the xref Monarch/MONDO use). The report text itself is not exposed through the API
(content is empty), so this source yields name matches / NORD ids (synonyms are not
kept: xrefs must be CURIEs), no relations to phenotypes or genes.

Groups: for a NORD id the report page (the record's `link`) is read up to its "Patient
Organizations" section, which lists the organisations NORD associates with the disease
(<div data-id="orgs" class="rdd-single-section"> with single-rd-resource blocks, each linking /organizations/<slug>/). The
robots.txt of 2026-05 allows all agents (Content-Signal: search=yes, ai-input=yes,
ai-train=no). The page is ~5 MB (menus), so it is streamed and closed after that
section. Organisations become NORD.ORG:<slug> ("patient_organisation").
Requests are throttled to respect the site's Crawl-delay.
"""
import html
import re
import time

from .base import Edge, Node, Source

API = "https://rarediseases.org/wp-json/wp/v2/rare-diseases"
FIELDS = "id,slug,link,title,synonyms"
MIN_INTERVAL = 1.0  # seconds between requests
_ORG = re.compile(r'<div class="single-rd-resource-headline">\s*<h5[^>]*>\s*'
                  r'<a href="https://rarediseases\.org/organizations/([^/"]+)/?">(.*?)</a>', re.S)
_SECTION = 'data-id="orgs" class="rdd-single-section"'
_NEXT = 'class="rdd-single-section"'


class NordSource(Source):
    name = "nord"
    id_prefixes = frozenset({"NORD"})
    by_name = True

    def __init__(self):
        super().__init__()
        self._last = 0.0

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            if node.kind not in ("disease", "unknown", "term"):
                return []
            return self._query(node, limit)
        except Exception:
            return []

    def _get(self, url, **params):
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            return self.get_json(url, params={"_fields": FIELDS, **params})
        finally:
            self._last = time.monotonic()

    def _mk(self, d: dict) -> Node:
        return Node(html.unescape(d["title"]["rendered"]), id=f"NORD:{d['id']}",
                    kind="disease", source=self.name,
                    info={"url": d["link"]} if d.get("link") else {})

    def _query(self, node: Node, limit: int) -> list[Edge]:
        for cand in self.ids_for(node):
            d = self._get(f"{API}/{cand.split(':', 1)[1]}")
            edges = [Edge(node, self._mk(d), "matches", self.name)]
            try:
                orgs = self._organisations(d["link"])
            except Exception:
                orgs = []
            edges += [Edge(node, o, "patient_organisation", self.name) for o in orgs]
            return edges[:max(limit, 1)]
        if node.id is not None:
            return []
        items = self._get(API, search=node.label, per_page=min(max(limit, 1), 100))
        return [Edge(node, self._mk(d), "matches", self.name) for d in items[:limit]]

    def _organisations(self, url: str) -> list[Node]:
        """Patient organisations listed on a NORD report page."""
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        buf = ""
        try:
            with self.session.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                r.encoding = r.encoding or "utf-8"
                for chunk in r.iter_content(65536, decode_unicode=True):
                    buf += chunk
                    i = buf.find(_SECTION)
                    if i >= 0 and buf.find(_NEXT, i + len(_SECTION)) >= 0:
                        break  # the section after it has started
        finally:
            self._last = time.monotonic()
        i = buf.find(_SECTION)
        if i < 0:
            return []
        end = buf.find(_NEXT, i + len(_SECTION))
        section = buf[i: end if end >= 0 else len(buf)]
        out, seen = [], set()
        for slug, name in _ORG.findall(section):
            if slug not in seen:
                seen.add(slug)
                out.append(Node(html.unescape(re.sub(r"<[^>]+>", "", name)).strip(),
                                f"NORD.ORG:{slug}", "patient_organisation", self.name,
                                info={"url": f"https://rarediseases.org/organizations/{slug}/"}))
        return out
