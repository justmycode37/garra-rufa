"""Offline fixtures for the fetcher boundary, ranking, evidence, and HTML safety."""

import json
import unittest
from copy import deepcopy
from pathlib import Path

from garra.connections import build_connections, render_connections

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "connections-demo.json"


class ConnectionsTests(unittest.TestCase):
    def setUp(self):
        self.p = json.loads(FIXTURE.read_text())

    def test_sections_and_linked_papers(self):
        out = build_connections(self.p)
        card = out["cards"][0]
        self.assertEqual(card["similarity"]["display_score"], 68)
        self.assertEqual(card["biological_status"], "supported_relationship")
        self.assertEqual(card["claims"][1]["status"], "conflicting_evidence")
        self.assertEqual(card["papers"][0]["linked_to"], ["C1", "C2"])
        self.assertTrue(card["assets"][0]["reuse_checks"])
        self.assertIn("SYNTHETIC DEMO", render_connections(out))
        json.dumps(out)

    def test_does_not_mutate_packet(self):
        before = deepcopy(self.p)
        build_connections(self.p)
        self.assertEqual(before, self.p)

    def test_threshold_boundary_and_empty_result(self):
        self.p["candidates"][0]["similarity"]["value"] = 0.5
        self.assertEqual(len(build_connections(self.p)["cards"]), 1)
        self.assertEqual(build_connections(self.p, min_score=51)["status"], "no_matches")

    def test_native_metric_not_mislabeled_as_percentage(self):
        s = self.p["candidates"][0]["similarity"]
        s.update(metric="ancestor_information_content", value=12.3)
        card = build_connections(self.p)["cards"][0]
        self.assertIsNone(card["similarity"]["display_score"])
        self.assertFalse(card["similarity"]["threshold_applied"])
        self.assertIn("native units", card["similarity"]["display_label"])

    def test_same_gene_is_insufficient(self):
        c = self.p["candidates"][0]
        c["claims"] = [c["claims"][0]]
        c["claims"][0]["relationship"] = "shared_gene"
        self.assertEqual(
            build_connections(self.p)["cards"][0]["biological_status"], "hypothesis_only"
        )

    def test_unreviewed_claim_is_hypothesis(self):
        self.p["candidates"][0]["claims"][0]["reviewed"] = False
        self.assertEqual(
            build_connections(self.p)["cards"][0]["biological_status"], "hypothesis_only"
        )

    def test_supported_claim_needs_evidence(self):
        self.p["candidates"][0]["claims"][0]["supporting_evidence_ids"] = []
        with self.assertRaises(ValueError):
            build_connections(self.p)

    def test_unlinked_papers_are_not_shown(self):
        extra = deepcopy(self.p["papers"][0])
        extra["id"] = "P2"
        self.p["papers"].append(extra)
        self.assertEqual(len(build_connections(self.p)["cards"][0]["papers"]), 1)

    def test_invalid_reference_rejected(self):
        self.p["candidates"][0]["assets"][0]["evidence_ids"] = ["absent"]
        with self.assertRaises(ValueError):
            build_connections(self.p)

    def test_asset_requires_reuse_checks(self):
        self.p["candidates"][0]["assets"][0]["reuse_checks"] = []
        with self.assertRaises(ValueError):
            build_connections(self.p)

    def test_equivalent_disease_excluded(self):
        self.p["anchor"]["equivalent_ids"].append("DEMO:B")
        out = build_connections(self.p)
        self.assertFalse(out["cards"])
        self.assertEqual(out["omitted"][0]["reason"], "same_disease_or_reviewed_equivalent")

    def test_invalid_score_values(self):
        for value in [float("nan"), float("inf"), -0.1, 1.1, True]:
            with self.subTest(value=value):
                self.p["candidates"][0]["similarity"]["value"] = value
                with self.assertRaises(ValueError):
                    build_connections(self.p)

    def test_mixed_metrics_rejected(self):
        extra = deepcopy(self.p["candidates"][0])
        extra["id"] = "DEMO:C"
        extra["similarity"]["metric"] = "ancestor_information_content"
        self.p["candidates"].append(extra)
        with self.assertRaises(ValueError):
            build_connections(self.p)

    def test_stable_rank_and_limit(self):
        extra = deepcopy(self.p["candidates"][0])
        extra["id"] = "DEMO:C"
        extra["similarity"]["value"] = 0.9
        self.p["candidates"].append(extra)
        out = build_connections(self.p, limit=1)
        self.assertEqual(out["cards"][0]["id"], "DEMO:C")
        self.assertEqual(out["omitted"][0]["reason"], "outside_result_limit")

    def test_html_escapes_external_content(self):
        self.p["candidates"][0]["name"] = "<script>alert(1)</script>"
        html = render_connections(build_connections(self.p))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_unsafe_links_rejected(self):
        self.p["papers"][0]["url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            build_connections(self.p)

    def test_full_text_requires_verified_open_access(self):
        self.p["papers"][0]["full_text_url"] = "https://example.org/full"
        with self.assertRaises(ValueError):
            build_connections(self.p)
        self.p["papers"][0]["access"] = "open_access"
        self.assertIn("Read open-access full text", render_connections(build_connections(self.p)))

    def test_no_claims_is_explicit_gap(self):
        self.p["candidates"][0].update(claims=[], assets=[])
        card = build_connections(self.p)["cards"][0]
        self.assertEqual(card["biological_status"], "hypothesis_only")
        self.assertFalse(card["papers"])


if __name__ == "__main__":
    unittest.main()
