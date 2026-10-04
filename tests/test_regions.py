import tempfile
import unittest
from pathlib import Path

from garra.discovery import DiscoveryEngine
from garra.discovery.regions import RegionIndex
from garra.ui.service import InputError


class RegionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        ontology = Path(self.temp.name) / "hp.obo"
        ontology.write_text(
            "[Term]\nid: HP:0000001\nname: Eye\n\n[Term]\nid: HP:0000002\nname: Iris\nis_a: HP:0000001\n\n[Term]\nid: HP:0000003\nname: Iris feature\nis_a: HP:0000002\n\n[Term]\nid: HP:0000004\nname: Heart\n"
        )
        bridge = {
            "schema_version": 1,
            "nodes": [
                {"id": "MONDO:1", "kind": "disease", "labels": ["One"], "identity_verified": True},
                {"id": "MONDO:2", "kind": "disease", "labels": ["Two"], "identity_verified": True},
            ],
            "claims": [],
            "papers": [],
            "candidates": [
                {
                    "disease_a": "MONDO:1",
                    "disease_b": "MONDO:2",
                    "shared_process_ids": [],
                    "phenotype_similarity": {"metric": "exact_hpo_jaccard", "value": 0.6},
                    "assessment": {},
                    "candidate_claim_ids": [],
                }
            ],
            "source_relations": [
                {"from": "MONDO:1", "to": "HP:0000003", "relation": "has_phenotype"},
                {"from": "MONDO:2", "to": "HP:0000004", "relation": "has_phenotype"},
            ],
        }
        self.index = RegionIndex(
            DiscoveryEngine(bridge),
            bridge,
            {
                "eyes": {"label": "Eye", "hpo": "HP:0000001"},
                "iris": {"label": "Iris", "hpo": "HP:0000002"},
                "heart": {"label": "Heart", "hpo": "HP:0000004"},
            },
            ontology,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_descendant_mapping_and_candidate_region_connection(self):
        detail = self.index.detail("iris")
        self.assertEqual([d["id"] for d in detail["diseases"]], ["MONDO:1"])
        self.assertEqual(detail["diseases"][0]["matched_phenotypes"][0]["id"], "HP:0000003")
        self.assertEqual(detail["related_regions"][0]["id"], "heart")
        self.assertEqual(detail["clusters"][0]["region_member_ids"], ["MONDO:1"])
        self.assertEqual(
            self.index.detail("eyes")["diseases"][0]["id"], detail["diseases"][0]["id"]
        )

    def test_atlas_contract_and_validation(self):
        graph = self.index.atlas({"region": "HP:0000002"})
        self.assertEqual(graph["total"], 1)
        self.assertEqual(graph["root"], "HP:0000002")
        self.assertTrue(any(e["relation"] == "has_descendant" for e in graph["edges"]))
        self.assertEqual(self.index.atlas({"region": "HP:0000002", "query": "absent"})["total"], 0)
        for body in ({"region": "HP:9999999"}, {"region": "HP:0000002", "limit": True}):
            with self.assertRaises(InputError):
                self.index.atlas(body)
        with self.assertRaises(InputError):
            self.index.detail("missing")

    def test_entity_expansion_keeps_only_recorded_connections(self):
        engine = self.index.engine
        graph = engine.entity_graph('MONDO:1')['graph']
        # Fixture does not include phenotype nodes; dangling endpoints are excluded.
        self.assertEqual([n['id'] for n in graph['nodes']], ['MONDO:1'])
        self.assertEqual(graph['edges'], [])
        self.assertIsNone(engine.entity_graph('missing'))
