"""chat_context: the graph chat's Markdown covers the whole graph within a budget, its most
important entries in full and the lower-ranked ones in one line."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "query-test"))
try:
    from chat_context import evidence_md, present_md
finally:
    sys.path.pop(0)


def evidence_view(n_edges=400):
    nodes = [{"id": f"n{i}", "label": f"entity {i}", "kind": "drug", "papers": ["P1"]} for i in range(n_edges + 1)]
    edges = [{"from": f"n{i}", "to": "n0", "relation": "treats", "level": "clinical_trial" if i < 5 else "in_vitro",
              "confidence": 1 - i / 1000, "papers": 3, "negative_papers": 0,
              "evidence": [{"paper": "P1", "quote": "a verbatim quote " * 8}]} for i in range(1, n_edges + 1)]
    cands = [{"id": f"n{i}", "label": f"entity {i}", "kind": "drug", "score": 1 - i / 100, "category": "direct",
              "papers": ["P1"], "paths": [{"edge": f"entity {i} treats entity 0", "level": "animal", "papers": 1,
                                           "confidence": .9, "bridge": "tested in the disease", "evidence": ["\"q\" (P1)"]}]}
             for i in range(1, 30)]
    return {"start": "n0", "disease": "entity 0", "settings": {"model": "m"}, "stats": {"screened": 9, "read": 3},
            "nodes": nodes, "edges": edges, "candidates": cands, "gaps": [],
            "papers": [{"key": "P1", "title": "A paper", "summary": "What it found."}], "paper_links": []}


class ChatContextTests(unittest.TestCase):
    def test_small_graph_is_complete(self):
        md = evidence_md(evidence_view(20))
        self.assertNotIn("not included", md)
        self.assertIn("**entity 1**", md)
        self.assertIn('entity 20 —treats→ entity 0 (in_vitro, 3 papers, confidence 0.98): "a verbatim quote', md)
        self.assertIn("**P1** A paper", md)

    def test_budget_keeps_the_strongest_in_full_and_names_the_rest(self):
        md = evidence_md(evidence_view(400), budget=40_000)
        self.assertLessEqual(len(md), 42_000)
        edges = md.split("## Evidence edges")[1].split("\n## ")[0]
        self.assertIn('entity 1 —treats→ entity 0 (clinical_trial, 3 papers, confidence 0.999): "', edges)  # strongest, full
        self.assertRegex(edges, r"\n- entity \d+ —treats→ entity 0 \(in_vitro, 3\)\n")  # weaker, one line
        self.assertIn("lower-ranked entries not included", edges)
        self.assertIn("## All entities by kind", md)  # every part is still there

    def test_overview_lists_every_entry_with_its_connections(self):
        view = {"focus": {"label": "D", "description": "About D.", "facts": [{"label": "Gene(s)", "value": "G"}], "synonyms": []},
                "sections": [{"id": "symptoms", "label": "Symptoms"}],
                "groups": [{"id": "g", "label": "Muscles", "section": "symptoms", "items": ["a", "b"], "count": 2}],
                "items": {"a": {"id": "a", "label": "Weakness", "kind": "phenotype", "frequency": "always", "notes": [], "sources": ["HPO"]},
                          "b": {"id": "b", "label": "Cramps", "kind": "phenotype", "lay": "Painful muscle tightening", "notes": []}},
                "links": [{"a": "a", "b": "b", "label": "co-occurs with"}], "notes": []}
        md = present_md(view)
        self.assertIn("- **Weakness** (phenotype) — always; sources: HPO\n  - connections: co-occurs with: Cramps", md)
        self.assertIn("- **Cramps** (phenotype) — Painful muscle tightening", md)


if __name__ == "__main__":
    unittest.main()
