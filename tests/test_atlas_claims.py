import unittest

from garra.discovery import DiscoveryEngine


class AtlasClaimsTests(unittest.TestCase):
    def engine(self):
        return DiscoveryEngine({
            "schema_version": 1, "candidates": [],
            "nodes": [{"id": key, "kind": kind, "labels": [key]} for key, kind in [
                ("MONDO:1", "disease"), ("MONDO:2", "disease"), ("HGNC:1", "gene"),
                ("HP:1", "phenotype"), ("GO:1", "process")]],
            "source_relations": [
                {"from": "HGNC:1", "to": "MONDO:1", "relation": "causes"},
                {"from": "MONDO:1", "to": "HP:1", "relation": "has_phenotype"},
                {"from": "MONDO:2", "to": "HP:1", "relation": "has_phenotype"}],
            "papers": [{"id": "PMID:1", "identifiers": ["PMID:1", "PMC123"],
                        "records": [{"meta": {"title": "Gene function study"}}]}],
            "claims": [{"id": "c1", "subject": "HGNC:1", "object": "GO:1", "paper_id": "PMID:1",
                        "relationship": "participates_in", "direction": "unknown", "polarity": "asserted",
                        "reviewed": False, "passage": "An exact supporting passage.", "study_type": "review",
                        "context": {"model": "human"}, "limitations": ["No disease-specific effect."],
                        "locator": {"section": "Results"}}],
        })

    def test_claim_keeps_context_publication_and_explicit_roles(self):
        engine = self.engine()
        graph = engine.entity_graph("MONDO:1")["graph"]
        nodes = {n["id"]: n for n in graph["nodes"]}
        self.assertEqual(nodes["PMID:1"]["kind"], "paper")
        self.assertEqual(nodes["PMID:1"]["label"], "Gene function study")
        self.assertEqual(nodes["evidence:c1"]["claim"], engine.claims["c1"])
        self.assertFalse(nodes["evidence:c1"]["claim"]["reviewed"])
        self.assertEqual(nodes["evidence:c1"]["url"], "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/")
        self.assertEqual({e["relation"] for e in graph["edges"] if e["source"] == "PMID:1"},
                         {"contains_claim", "claim_subject", "claim_object"})
        self.assertTrue(all(e["from"] in nodes and e["to"] in nodes for e in graph["edges"]))
        nodes["evidence:c1"]["claim"]["reviewed"] = True
        self.assertFalse(engine.claims["c1"]["reviewed"])

    def test_shared_phenotype_does_not_attach_unrelated_paper(self):
        graph = self.engine().entity_graph("MONDO:2")["graph"]
        self.assertFalse(any(n["kind"] in {"paper", "claim"} for n in graph["nodes"]))

    def test_no_open_access_identifier_uses_publication_record(self):
        engine = self.engine()
        engine.papers["PMID:1"]["identifiers"] = ["PMID:1"]
        paper = next(n for n in engine.entity_graph("MONDO:1")["graph"]["nodes"] if n["kind"] == "paper")
        self.assertEqual(paper["url"], "https://pubmed.ncbi.nlm.nih.gov/1/")
        self.assertIn("not verified", paper["access"])
