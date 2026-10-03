"""NORD (National Organization for Rare Disorders, https://rarediseases.org).

NORD has no documented public API, but its WordPress site exposes the standard
read-only JSON REST endpoint (not disallowed in robots.txt):
  https://rarediseases.org/wp-json/wp/v2/rare-diseases?search=...   (list/search)
  https://rarediseases.org/wp-json/wp/v2/rare-diseases/{id}         (by NORD id)
The post id equals the NORD identifier (Marfan syndrome = NORD:1403, which is also
the xref Monarch/MONDO use). The report text itself is not exposed through the API
(content is empty) and NORD's terms discourage scraping the HTML, so this source
only yields name matches / NORD ids (synonyms are not kept: xrefs must be CURIEs), no relations to
phenotypes or genes. Requests are throttled to respect the site's Crawl-delay.
"""
import html
import time

from .base import Edge, Node, Source

API = "https://rarediseases.org/wp-json/wp/v2/rare-diseases"
FIELDS = "id,slug,link,title,synonyms"
MIN_INTERVAL = 1.0  # seconds between requests


class NordSource(Source):
    name = "nord"

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
                    kind="disease", source=self.name)

    def _query(self, node: Node, limit: int) -> list[Edge]:
        for cand in (node.id, *node.xrefs):
            if cand and cand.upper().startswith("NORD:"):
                d = self._get(f"{API}/{cand.split(':', 1)[1]}")
                return [Edge(node, self._mk(d), "matches", self.name)]
        items = self._get(API, search=node.label, per_page=min(max(limit, 1), 100))
        return [Edge(node, self._mk(d), "matches", self.name) for d in items[:limit]]
