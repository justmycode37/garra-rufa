"""present.query_view: a symptom search is shown as its ranked candidates side by side."""
import sys
import unittest
from pathlib import Path

QUERY_TEST = Path(__file__).resolve().parents[1] / "src" / "query-test"
sys.path.insert(0, str(QUERY_TEST))
try:
    from present import Graph, query_view
    from sources import _hpoa
    HPO = _hpoa.PICKLE.exists() and _hpoa.load() is not None
except ImportError:  # requests missing: the query-test pipeline is not installed
    HPO = False
finally:
    sys.path.pop(0)


def match(q, label, freq):
    return {"query": q, "query_label": label, "matched": q, "matched_label": label,
            "full": True, "frequency": freq}


@unittest.skipUnless(HPO, "needs the local HPO data (data/hpo/)")
class QueryViewTests(unittest.TestCase):
    def setUp(self):
        heart, pain = match("HP:0001627", "Abnormal heart morphology", 1.0), match("HP:0100749", "Chest pain", 0.5)
        ranking = [
            {"node": "OMIM:617223", "id": "OMIM:617223", "name": "Sudden cardiac failure, alcohol-induced",
             "xrefs": [], "score": 4.4, "genes": [], "matches": [heart, pain]},
            {"node": "OMIM:301163", "id": "OMIM:301163", "name": "Cardiomyopathy, dilated, 3C",
             "xrefs": [], "score": 4.1, "genes": [], "matches": [heart, pain]},
            {"node": "MONDO:0009144", "id": "ORPHA:1880", "name": "Ebstein malformation of the tricuspid valve",
             "xrefs": [], "score": 2.0, "genes": [], "matches": [heart]},
        ]
        start = "term:hp:0001627, chest pain"
        self.view = query_view(Graph({
            "start": start, "focus": ["OMIM:617223"],
            "query": {"text": "HP:0001627, chest pain", "mode": "candidates", "ranking": ranking, "parts": [
                {"text": "HP:0001627", "kind": "phenotype", "id": "HP:0001627", "label": "Abnormal heart morphology"},
                {"text": "chest pain", "kind": "phenotype", "id": "HP:0100749", "label": "Chest pain"}]},
            "nodes": [{"id": start, "label": "HP:0001627, chest pain", "kind": "query", "xrefs": []},
                      *({"id": r["node"], "label": r["name"], "kind": "disease", "xrefs": []} for r in ranking)],
            "edges": []}))

    def test_the_search_is_the_centre_and_every_candidate_is_shown(self):
        v = self.view
        self.assertEqual(v["focus"]["label"], "Abnormal heart morphology · Chest pain")
        candidates = [i for i in v["items"].values() if i["section"] == "candidates"]
        self.assertEqual(len(candidates), 3)
        groups = {g["label"]: g["items"] for g in v["groups"] if g["section"] == "candidates"}
        self.assertEqual(groups["Match all 2 terms"], ["OMIM:617223", "OMIM:301163"])
        self.assertEqual(groups["Match 1 of 2 terms"], ["MONDO:0009144"])

    def test_view_is_consistent(self):
        v = self.view
        self.assertTrue({"HP:0001627", "HP:0100749"} <= set(v["items"]))
        self.assertIn(("HP:0100749", "OMIM:617223"), {(lk["a"], lk["b"]) for lk in v["links"]})
        for lk in v["links"]:
            self.assertIn(lk["a"], v["items"])
            self.assertIn(lk["b"], v["items"])
        grouped = [i for g in v["groups"] for i in g["items"]]
        self.assertCountEqual(grouped, v["items"])
        self.assertEqual({s["id"] for s in v["sections"]}, {g["section"] for g in v["groups"]})
        self.assertTrue(any(i["label"] == "PPA2" for i in v["items"].values()))  # causal gene


if __name__ == "__main__":
    unittest.main()
