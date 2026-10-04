"""Offline identity, provenance and disease-pair inference checks."""
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from garra.atlas.cli import main as atlas_main
from garra.bridge import build_bridge


def fixture():
    source = {"nodes": [
        {"id": "MONDO:1", "label": "Alpha", "kind": "disease"},
        {"id": "MONDO:2", "label": "Beta", "kind": "disease"},
        {"id": "GO:1", "label": "Process", "kind": "process"},
        {"id": "HP:1", "label": "Feature one", "kind": "phenotype"},
        {"id": "HP:2", "label": "Feature two", "kind": "phenotype"}],
        "edges": [{"from": "MONDO:1", "to": "HP:1", "relation": "has_phenotype"},
                  {"from": "MONDO:2", "to": "HP:2", "relation": "has_phenotype"}]}
    evidence = {"nodes": copy.deepcopy(source["nodes"][:3]), "papers": [], "edges": []}
    for n in (1, 2):
        evidence["papers"].append({"key": f"PMID:{n}", "meta": {"pmid": str(n)}})
        evidence["edges"].append({"from": f"MONDO:{n}", "to": "GO:1", "relation": "involves",
                                 "evidence": [{"paper": f"PMID:{n}", "quote": "synthetic test passage",
                                               "passage": "P1", "section": "Results",
                                               "verification": "exact_passage", "direction": "decreased",
                                               "polarity": "asserted", "organism": "human"}]})
    return source, evidence


class BridgeTests(unittest.TestCase):
    def test_effect_candidates_survive_zero_phenotype_similarity(self):
        result = build_bridge(*fixture())
        candidate = result["candidates"][0]
        self.assertEqual(candidate["phenotype_similarity"]["value"], 0)
        self.assertIn("paper_reported_effect_alignment", candidate["candidate_reasons"])
        self.assertEqual(candidate["assessment"]["status"], "hypothesis")
        self.assertFalse(candidate["assessment"]["reviewed"])
        self.assertTrue(all(not c["reviewed"] for c in result["claims"]))
        self.assertEqual(len(candidate["assessment"]["supporting_claim_ids"]), 2)

    def test_opposed_effects_and_context_differences_are_preserved(self):
        s, e = fixture()
        e["edges"][1]["evidence"][0].update(direction="increased", organism="mouse")
        result = build_bridge(s, e)["candidates"][0]["assessment"]
        self.assertEqual(result["status"], "conflicting_evidence")
        self.assertEqual(len(result["contradicting_claim_ids"]), 2)
        self.assertEqual(result["comparisons"][0]["context_differences"], ["organism"])

    def test_unknown_direction_does_not_become_alignment(self):
        s, e = fixture()
        e["edges"][1]["evidence"][0].pop("direction")
        self.assertEqual(build_bridge(s, e)["candidates"][0]["assessment"]["status"],
                         "insufficient_evidence")

    def test_negated_claim_is_not_support(self):
        s, e = fixture()
        e["edges"][1]["evidence"][0]["polarity"] = "negated"
        result = build_bridge(s, e)["candidates"][0]["assessment"]
        self.assertEqual(result["supporting_claim_ids"], [])
        self.assertEqual(result["status"], "conflicting_evidence")

    def test_legacy_quotes_require_reverification(self):
        s, e = fixture()
        for edge in e["edges"]:
            edge["evidence"][0].pop("verification")
        result = build_bridge(s, e)
        self.assertEqual(result["claims"], [])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["issues"]), 2)

    def test_equal_labels_and_xrefs_do_not_merge(self):
        s, e = fixture()
        s["nodes"][1].update(label="Alpha", xrefs=["MONDO:1"])
        s["edges"].append({"from": "MONDO:2", "to": "MONDO:1", "relation": "subclass_of"})
        r = build_bridge(s, e)
        self.assertEqual(len([n for n in r["nodes"] if n["kind"] == "disease"]), 2)
        self.assertIn("subclass_of", [x["relation"] for x in r["source_relations"]])

    def test_reviewed_mapping_cannot_override_subtype(self):
        s, e = fixture()
        s["edges"].append({"from": "MONDO:2", "to": "MONDO:1", "relation": "subclass_of"})
        m = {"from": "MONDO:2", "to": "MONDO:1", "relation": "same_entity",
             "reviewed": True, "source_url": "https://example.org/mapping"}
        r = build_bridge(s, e, mappings=[m])
        self.assertEqual(r["identity_links"], [])
        self.assertTrue(r["issues"])

    def test_explicit_verified_mapping_joins_cross_graph_ids(self):
        s, e = fixture()
        e["nodes"][0]["id"] = "ORPHA:1"
        e["edges"][0]["from"] = "ORPHA:1"
        m = {"from": "MONDO:1", "to": "ORPHA:1", "relation": "same_entity",
             "reviewed": True, "source_url": "https://example.org/mapping"}
        r = build_bridge(s, e, mappings=[m])
        self.assertEqual(r["claims"][0]["subject"], "MONDO:0000001")
        self.assertEqual(len(r["identity_links"]), 1)

    def test_fuzzy_identifiers_stay_unresolved(self):
        s, e = fixture()
        e["nodes"][0]["how"] = "fuzzy"
        r = build_bridge(s, e)
        self.assertTrue(r["claims"][0]["subject"].startswith("unresolved:"))
        self.assertEqual(r["candidates"], [])

    def test_publication_alias_bridge_deduplicates_all_identifiers(self):
        s, e = fixture()
        e["papers"] = [{"key": "old-doi", "doi": "https://doi.org/10.1234/ABC"},
                       {"key": "PMID:1", "pmid": "1"},
                       {"key": "joined", "pmid": "1", "doi": "10.1234/abc", "pmcid": "PMC9"}]
        r = build_bridge(s, e)
        self.assertEqual(len(r["papers"]), 1)
        self.assertEqual(set(r["papers"][0]["identifiers"]),
                         {"PMID:1", "DOI:10.1234/abc", "PMC9"})

    def test_conflicting_publication_ids_fail_explicitly(self):
        s, e = fixture()
        e["papers"] = [{"pmid": "1", "doi": "10.1234/x"}, {"pmid": "2", "doi": "10.1234/x"}]
        with self.assertRaises(ValueError):
            build_bridge(s, e)

    def test_pathway_candidates_without_papers_are_not_supported(self):
        s, _ = fixture()
        s["edges"] += [{"from": f"MONDO:{n}", "to": "GO:1", "relation": "involves"}
                       for n in (1, 2)]
        r = build_bridge(s, {"nodes": [], "edges": [], "papers": []})
        self.assertEqual(r["candidates"][0]["assessment"]["status"], "insufficient_evidence")

    def test_cli_writes_bridge_contract(self):
        source, evidence = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source.json").write_text(json.dumps(source))
            (root / "evidence.json").write_text(json.dumps(evidence))
            rc = atlas_main(["bridge", str(root / "source.json"), str(root / "evidence.json"),
                             "--out", str(root / "result.json")])
            self.assertEqual(rc, 0)
            result = json.loads((root / "result.json").read_text())
            self.assertEqual(len(result["candidates"]), 1)
            self.assertEqual(len(result["claims"]), 2)


HAS_REQUESTS = importlib.util.find_spec("requests") is not None
if HAS_REQUESTS:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "query-test"))
    from entities import Entities
    from evidence.extract import clean_result
    from evidence.verify import Verifier
    from sources.base import Node
    sys.path.pop(0)


@unittest.skipUnless(HAS_REQUESTS, "Install .[research]")
class ExtractionTests(unittest.TestCase):
    def test_dropped_negation_is_not_a_verified_quote(self):
        doc = SimpleNamespace(passages=[SimpleNamespace(id="P1", text=
            "The treatment did not increase enzyme activity in the patient cells.")])
        v = Verifier(doc)
        self.assertIsNone(v.check("The treatment did increase enzyme activity in the patient cells.", "P1"))
        self.assertEqual(v.check(doc.passages[0].text, "P1"), "P1")
        self.assertIsNone(v.check(doc.passages[0].text, "P2"))

    def test_claim_context_survives_cleaning_and_cannot_be_reviewed_by_model(self):
        out = {"entities": [{"key": "a", "name": "Alpha", "type": "disease"},
                            {"key": "p", "name": "Process", "type": "process"}],
               "edges": [{"subject": "a", "object": "p", "predicate": "involves",
                          "direction": "decreased", "polarity": "asserted", "model": "patient cells",
                          "variant": "unknown", "tissue": "muscle", "reviewed": True,
                          "evidence": [{"passage": "P1", "quote": "An exact passage from a paper"}]}]}
        _, edges, _ = clean_result(out)
        self.assertFalse(edges[0]["reviewed"])
        self.assertEqual(edges[0]["direction"], "decreased")
        self.assertEqual(edges[0]["model"], "patient cells")
        self.assertEqual(edges[0]["population"], "unknown")

    def test_source_graph_does_not_merge_gene_symbols_or_bare_xrefs(self):
        ents = Entities()
        a = ents.add(Node("GENE", "HGNC:1", "gene", xrefs=("NCBIGene:2",)))
        b = ents.add(Node("GENE", "NCBIGene:2", "gene"))
        self.assertNotEqual(ents.find(a), ents.find(b))

    def test_extraction_through_graph_to_pair_assessment(self):
        # Controlled model responses test integration without paid model calls or network.
        from evidence.build import Graph
        from evidence.fulltext import Doc, Passage
        from evidence.main import read_paper

        source, _ = fixture()
        profile = SimpleNamespace(disease=source["nodes"][0], id="MONDO:1",
                                  kinds={n["id"]: n["kind"] for n in source["nodes"]})
        graph = Graph(profile)
        records = []
        for i, name in [(1, "Alpha"), (2, "Beta")]:
            quote = f"{name} reduces the measured Process activity in patient cells."
            model_result = {"entities": [{"key": "d", "name": name, "type": "disease"},
                                         {"key": "p", "name": "Process", "type": "process"}],
                            "edges": [{"subject": "d", "object": "p", "predicate": "affects",
                                       "direction": "decreased", "polarity": "asserted",
                                       "organism": "human", "model": "patient cells",
                                       "evidence": [{"passage": "P1", "quote": quote}]}]}
            doc = Doc("abstract", [Passage("P1", "Abstract", quote)])
            normalizer = SimpleNamespace(resolve=lambda label, kind, *args:
                {"id": {"Alpha": "MONDO:1", "Beta": "MONDO:2", "Process": "GO:1"}[label],
                 "label": label, "how": "ontology"})
            rec = read_paper({"pmid": str(i), "title": "Synthetic test"},
                             llm=SimpleNamespace(chat=lambda *a, **kw: model_result),
                             profile_text="Synthetic test", fulltext=SimpleNamespace(get=lambda *a: doc),
                             normalizer=normalizer, max_chars=10000)
            graph.add_paper(rec)
            records.append(rec)
        result = build_bridge(source, {"nodes": list(graph.nodes.values()),
                                       "edges": list(graph.edges.values()), "papers": records})
        self.assertEqual(len(result["claims"]), 2)
        self.assertEqual(result["candidates"][0]["assessment"]["status"], "hypothesis")
        self.assertEqual(result["claims"][0]["context"]["model"], "patient cells")
