import json
import tempfile
import unittest
from pathlib import Path

from garra.bridge import build_bridge
from garra.discovery import DiscoveryEngine
from garra.discovery.build_snapshot import collect


class SnapshotTests(unittest.TestCase):
    def test_exact_association_subjects_verified_genes_and_negation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "opentargets").mkdir()
            phenotypes = [
                {
                    "subject": disease,
                    "object": "HP:0001250",
                    "predicate": "biolink:has_phenotype",
                    "subject_label": disease,
                    "object_label": "Seizure",
                    "negated": False,
                }
                for disease in ["MONDO:0000002", "MONDO:0000003"]
            ]
            phenotypes.append({**phenotypes[0], "object": "HP:0001252", "negated": True})
            (root / "monarch_anchor_phenotypes.json").write_text(
                json.dumps(
                    {
                        "body": {
                            "mondo_id": "MONDO:0000001",
                            "raw": {"items": phenotypes, "total": 4},
                        }
                    }
                )
            )
            genes = [
                {"subject": "HGNC:1", "object": d, "predicate": "biolink:causes"}
                for d in ["MONDO:0000002", "MONDO:0000003"]
            ]
            (root / "monarch_anchor_genes.json").write_text(
                json.dumps({"body": {"raw": {"items": genes}}})
            )
            (root / "opentargets" / "GENE.json").write_text(
                json.dumps(
                    {
                        "body": {
                            "symbol": "GENE",
                            "ensembl_id": "ENSG1",
                            "pathways": [{"pathwayId": "R-HSA-123", "pathway": "Example"}],
                        }
                    }
                )
            )
            source, inputs, warnings = collect(root, {})
            ids = {n["id"] for n in source["nodes"]}
            self.assertNotIn("MONDO:0000001", ids)
            self.assertNotIn("HP:0001252", ids)
            self.assertNotIn("Reactome:R-HSA-123", ids)
            self.assertEqual(len(inputs), 3)
            self.assertEqual(
                {w["reason"] for w in warnings},
                {"truncated_source_response", "unverified_gene_mapping"},
            )
            source, _, _ = collect(
                root,
                {
                    "ENSG1": {
                        "hgnc_id": "HGNC:1",
                        "symbol": "GENE",
                        "url": "https://rest.genenames.org/fetch/ensembl_gene_id/ENSG1",
                    }
                },
            )
            self.assertTrue(all(e["provenance"] for e in source["edges"]))
            bridge = build_bridge(source, {"nodes": [], "edges": [], "papers": []})
            engine = DiscoveryEngine(bridge)
            self.assertEqual(engine.clusters(kind="pathway")["total"], 1)
            self.assertEqual(engine.clusters(kind="phenotype")["total"], 1)
            self.assertEqual(
                bridge["candidates"][0]["assessment"]["status"], "insufficient_evidence"
            )
            self.assertEqual(bridge["claims"], [])
