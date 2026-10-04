import json
import tempfile
import unittest
from pathlib import Path

from garra.bridge import build_bridge
from garra.discovery import DiscoveryEngine
from garra.discovery.evidence_inputs import load_evidence
from garra.discovery.regions import RegionIndex


class NeighborsTests(unittest.TestCase):
    def test_sibling_terms_rank_without_changing_exact_clusters_or_matching_universal_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            ontology = Path(tmp) / "hp.obo"
            ontology.write_text("""[Term]
id: HP:0000001
name: Root

[Term]
id: HP:0000002
name: Specific parent
is_a: HP:0000001

[Term]
id: HP:0000003
name: Feature A
is_a: HP:0000002

[Term]
id: HP:0000004
name: Feature B
is_a: HP:0000002
""")
            bridge = {"schema_version": 1, "claims": [], "papers": [], "candidates": [],
                      "nodes": [{"id": f"MONDO:{i}", "kind": "disease", "labels": [str(i)], "identity_verified": True} for i in range(1, 5)],
                      "source_relations": [{"from": f"MONDO:{i}", "to": hp, "relation": "has_phenotype"} for i, hp in [(1, "HP:0000003"), (2, "HP:0000004"), (3, "HP:0000001")]]}
            engine = DiscoveryEngine(bridge)
            regions = RegionIndex(engine, bridge, {"all": {"hpo": "HP:0000001", "label": "All"}}, ontology)
            response = regions.neighbors("MONDO:1")
            self.assertEqual([c["id"] for c in response["candidates"]], ["MONDO:2"])
            candidate = response["candidates"][0]
            self.assertEqual(candidate["exact_hpo_jaccard"], 0)
            self.assertGreater(candidate["score"], 0)
            self.assertLessEqual(candidate["score"], 1)
            self.assertEqual(candidate["shared_features"][0]["id"], "HP:0000002")
            self.assertFalse(candidate["shared_features"][0]["exact_in_both"])
            self.assertEqual(engine.clusters()["total"], 0)
            self.assertEqual(regions.neighbors("MONDO:4")["annotation_status"], "not_annotated_in_snapshot")
            self.assertEqual(regions.neighbors("MONDO:3")["candidates"], [])


class EvidenceInputsTests(unittest.TestCase):
    def test_duplicate_files_do_not_double_import_and_legacy_quotes_remain_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = {"nodes": [{"id": "HGNC:1", "kind": "gene", "label": "G"}, {"id": "GO:0000001", "kind": "process", "label": "P"}],
                   "edges": [{"from": "HGNC:1", "to": "GO:0000001", "relation": "participates_in", "evidence": [{"paper": "p", "quote": "legacy quote"}]}],
                   "papers": [{"key": "p", "meta": {"pmid": "123"}}]}
            for name in ("one.kg.json", "copy.kg.json"):
                (root / name).write_text(json.dumps(doc))
            combined, inventory = load_evidence(list(root.glob("*.json")))
            self.assertEqual(len(combined["papers"]), 1)
            self.assertEqual(sum(i["status"] == "duplicate_file" for i in inventory), 1)
            bridge = build_bridge({"nodes": [], "edges": []}, combined)
            self.assertEqual(bridge["claims"], [])
            self.assertTrue(bridge["issues"])

    def test_multi_file_paper_keys_and_unresolved_entities_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for number in (1, 2):
                doc = {"nodes": [{"id": "HGNC:1", "kind": "gene", "label": "G"}, {"id": "text:effect", "kind": "process", "label": f"Effect {number}", "how": "text"}],
                       "papers": [{"key": "paper", "meta": {"pmid": str(number)}}],
                       "edges": [{"from": "HGNC:1", "to": "text:effect", "relation": "affects", "evidence": [{"paper": "paper", "quote": f"Passage {number}", "verification": "exact_passage"}]}]}
                (root / f"{number}.kg.json").write_text(json.dumps(doc))
            combined, _ = load_evidence(list(root.glob("*.json")))
            bridge = build_bridge({"nodes": [], "edges": []}, combined)
            self.assertEqual(len(bridge["papers"]), 2)
            self.assertEqual({c["paper_id"] for c in bridge["claims"]}, {"PMID:1", "PMID:2"})
            self.assertEqual(len({c["object"] for c in bridge["claims"]}), 2)
            self.assertTrue(all(c["reviewed"] is False for c in bridge["claims"]))


class ClaimDedupTests(unittest.TestCase):
    def test_repeated_verified_claim_is_counted_once(self):
        evidence = {"nodes": [{"id": "HGNC:1", "kind": "gene", "label": "G"}, {"id": "GO:0000001", "kind": "process", "label": "P"}],
                    "papers": [{"key": "p", "meta": {"pmid": "1"}}]}
        edge = {"from": "HGNC:1", "to": "GO:0000001", "relation": "participates_in",
                "evidence": [{"paper": "p", "verification": "exact_passage", "quote": "Exact passage"}]}
        evidence["edges"] = [edge, edge]
        bridge = build_bridge({"nodes": [], "edges": []}, evidence)
        self.assertEqual(len(bridge["claims"]), 1)
        self.assertEqual(bridge["claims"][0]["id"], "claim:1")
