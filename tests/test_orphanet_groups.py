"""Offline tests of the orpha.net page cache (src/query-test/sources/orphanet_groups.py)."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HAS_REQUESTS = importlib.util.find_spec("requests") is not None
if HAS_REQUESTS:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "query-test"))
    try:
        from sources import _groups
        from sources import orphanet_groups as og
    finally:
        sys.path.pop(0)

CHALLENGE = ('<html lang="fr"><head><title>Vérification de la connexion...</title></head>'
             '<body><form method="post" target="post" action="/_challenge" name="challenge">'
             '</form></body></html>')
PAGE = '<div id="direct-relation"></div><div class="result-card"><a href="/en/x/y/1">A</a></div>'


@unittest.skipUnless(HAS_REQUESTS, "requests not installed")
class ChallengePageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        disk = _groups.PageCache("pages")
        disk.dir = Path(self.tmp.name)
        patches = [patch.object(og, "_disk", disk), patch.dict(og._pages, clear=True),
                   patch.object(og.THROTTLE, "wait", lambda: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.disk = disk

    def _session(self, text):
        s = MagicMock()
        s.get.return_value = MagicMock(status_code=200, content=text.encode("utf-8"))
        return s

    def test_challenge_is_not_cached(self):
        self.assertIsNone(og.fetch_page(self._session(CHALLENGE), "patient", "56"))
        self.assertIsNone(self.disk.get("patient:56"))

    def test_cached_challenge_is_refetched(self):
        self.disk.put("patient:56", CHALLENGE)
        s = self._session(PAGE)
        self.assertEqual(og.fetch_page(s, "patient", "56"), PAGE)
        s.get.assert_called_once()
        self.assertEqual(self.disk.get("patient:56"), PAGE)

    def test_real_page_is_cached(self):
        self.assertEqual(og.fetch_page(self._session(PAGE), "patient", "337"), PAGE)
        self.assertEqual(self.disk.get("patient:337"), PAGE)


if __name__ == "__main__":
    unittest.main()
