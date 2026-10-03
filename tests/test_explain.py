"""Explain layer: evidence packet and fallback (no live OpenAI in default tests)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from garra.explain.context import build_evidence_packet
from garra.explain.explain import explain_journey, fallback_explain
from garra.explain.fallback import fallback_narrative


SAMPLE_JOURNEY = {
    "status": "ok",
    "query": "Pompe disease",
    "resolve": {
        "status": "ok",
        "primary": {
            "kind": "disease",
            "disease_key": "MONDO:0009290",
            "name": "glycogen storage disease II",
        },
    },
    "similar_diseases": {
        "anchor": {"disease_key": "MONDO:0009290", "name": "glycogen storage disease II", "genes": ["GAA"]},
        "neighbors": [
            {
                "disease_key": "MONDO:0018485",
                "name": "late-onset Pompe",
                "confidence": "medium",
                "reasons": ["shared_gene"],
                "warnings": ["SAME_GENE_SUBTYPE"],
                "score": 0.4,
                "shared_genes": ["GAA"],
                "shared_hpo_count": 0,
                "shared_pathway_count": 0,
            }
        ],
        "coverage": {"phenotype_edges": False, "pathway_edges": False},
    },
    "actions": {
        "trials": {
            "records": [
                {
                    "nct_id": "NCT03694561",
                    "title": "Late-onset Pompe management",
                    "status": "ACTIVE_NOT_RECRUITING",
                    "conditions": ["Pompe Disease"],
                    "url": "https://clinicaltrials.gov/study/NCT03694561",
                }
            ],
            "filtered_out": [
                {
                    "nct_id": "NCT06557369",
                    "title": "AMD trial",
                    "conditions": ["Intermediate AMD"],
                    "warning": "TEXT_MISMATCH",
                }
            ],
        },
        "grants": {
            "records": [
                {
                    "project_num": "5R00HL161420-05",
                    "title": "Novel Adjunctive Therapies for Pompe Disease",
                    "pi": "ROGER, ANGELA L.",
                    "url": "https://reporter.nih.gov/project-details/11263644",
                }
            ]
        },
        "papers": None,
    },
    "next_steps": [{"action": "open_trial", "detail": "Review NCT03694561 eligibility.", "url": "https://clinicaltrials.gov/study/NCT03694561"}],
}


class ExplainTests(unittest.TestCase):
    def test_evidence_packet_has_citations(self):
        packet = build_evidence_packet(SAMPLE_JOURNEY)
        self.assertTrue(packet["citations"])
        ids = {c["id"] for c in packet["citations"]}
        self.assertIn("E1", ids)
        kinds = {c["kind"] for c in packet["citations"]}
        self.assertIn("neighbor", kinds)
        self.assertIn("clinical_trial", kinds)
        self.assertIn("coverage_gap", kinds)

    def test_fallback_narrative_mentions_uncertainty(self):
        packet = build_evidence_packet(SAMPLE_JOURNEY)
        narrative = fallback_narrative(packet)
        self.assertIn("summary_for_family", narrative)
        self.assertTrue(narrative["uncertainties"])
        self.assertEqual(narrative["mode"], "fallback")

    def test_explain_journey_offline(self):
        out = explain_journey(SAMPLE_JOURNEY, use_openai=False)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["narrative"]["mode"], "fallback")
        json.dumps(out)

    @patch("garra.explain.explain.call_openai")
    def test_explain_journey_openai_path(self, mock_openai):
        mock_openai.return_value = {
            "mode": "openai",
            "title": "Pompe path",
            "summary_for_family": "Summary [E1]",
            "sections": [],
            "uncertainties": [],
            "next_step_this_week": "Check trial",
        }
        out = explain_journey(SAMPLE_JOURNEY, use_openai=True)
        self.assertEqual(out["narrative"]["mode"], "openai")
        mock_openai.assert_called_once()

    @patch("garra.explain.explain.call_openai", side_effect=RuntimeError("no key"))
    def test_openai_failure_falls_back(self, _mock):
        out = explain_journey(SAMPLE_JOURNEY, use_openai=True)
        self.assertEqual(out["narrative"]["mode"], "fallback")
        self.assertIn("openai_error", out["narrative"])


if __name__ == "__main__":
    unittest.main()
