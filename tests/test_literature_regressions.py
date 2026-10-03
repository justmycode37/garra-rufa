"""Offline regressions for the optional research pipeline (install .[research])."""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HAS_REQUESTS = importlib.util.find_spec("requests") is not None
if HAS_REQUESTS:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "query-test"))
    try:
        from literature.base import Concept, Query
        from literature.litvar import LitVarProvider
        from literature.pubtator import PubtatorProvider
        from sources.base import Node
        from sources.orphadata import OrphadataSource
    finally:
        sys.path.pop(0)


@unittest.skipUnless(HAS_REQUESTS, "Install .[research] to test literature providers")
class LiteratureMatchingTests(unittest.TestCase):
    def disease(self, **kw):
        return Concept("TEST:subtype", "Specific subtype", "disease",
                       names=["Specific subtype"], **kw)

    def test_broader_mesh_and_multiple_matches_do_not_establish_identity(self):
        disease = self.disease(mesh="Broad disease", mesh_id="D000001", mesh_exact=False)
        provider = PubtatorProvider(None)
        row = {"name": "Broad disease", "db_id": "D000001",
               "_id": "@DISEASE_Broad", "match": "Multiple matches"}
        with patch.object(provider, "fetch_json", return_value=[row]):
            self.assertIsNone(provider.concept(disease))

    def test_exact_mesh_id_is_accepted(self):
        provider = PubtatorProvider(None)
        disease = self.disease(mesh="Specific subtype", mesh_id="D000002", mesh_exact=True)
        row = {"name": "Alternative label", "db_id": "D000002", "_id": "@DISEASE_Specific"}
        with patch.object(provider, "fetch_json", return_value=[row]):
            self.assertEqual(provider.concept(disease), "@DISEASE_Specific")

    def test_exact_name_is_accepted_without_mesh(self):
        provider = PubtatorProvider(None)
        with patch.object(provider, "fetch_json", return_value=[
            {"name": "Specific subtype", "_id": "@DISEASE_Specific"}
        ]):
            self.assertEqual(provider.concept(self.disease()), "@DISEASE_Specific")

    def resolve_variant(self, rows, **kw):
        variant = Concept("TEST:variant", "GENE1 variant", "variant", **kw)
        query = Query("q", "variant", self.disease(), variant)
        provider = LitVarProvider(None)
        with patch.object(provider, "fetch_json", return_value=rows):
            return provider._litvar_id(query)

    def test_wrong_same_gene_variant_is_rejected(self):
        row = {"gene": ["GENE1"], "_id": "wrong", "hgvs": "p.Arg20Trp"}
        self.assertIsNone(self.resolve_variant([row], gene="GENE1", hgvs="p.Arg10Gly"))

    def test_one_and_three_letter_substitutions_match(self):
        rows = [{"gene": ["GENE1"], "_id": "wrong", "hgvs": "p.R20W"},
                {"gene": ["GENE1"], "_id": "right", "hgvs": "p.R10G"}]
        result = self.resolve_variant(rows, gene="GENE1", hgvs="p.Arg10Gly")
        self.assertEqual(result[0], "right")

    def test_same_change_on_wrong_gene_is_rejected(self):
        self.assertIsNone(self.resolve_variant([
            {"gene": ["OTHER"], "_id": "wrong", "hgvs": "p.R10G"}
        ], gene="GENE1", hgvs="p.Arg10Gly"))

    def test_ambiguous_variant_identifiers_are_rejected(self):
        rows = [{"gene": ["GENE1"], "_id": key, "hgvs": "p.R10G"}
                for key in ["first", "second"]]
        self.assertIsNone(self.resolve_variant(rows, gene="GENE1", hgvs="p.Arg10Gly"))

    def test_explicit_rsid_does_not_use_autocomplete(self):
        self.assertEqual(self.resolve_variant([], rsid="rs123"), ("litvar@rs123##", "rs123"))

    def test_definition_timeout_preserves_phenotypes(self):
        provider = OrphadataSource()

        def code(path):
            if path.endswith("/Definition"):
                raise TimeoutError("optional definition unavailable")
            return None

        def data(path):
            if path.startswith("rd-phenotypes/"):
                return {"Disorder": {"HPODisorderAssociation": [
                    {"HPO": {"HPOTerm": "Example phenotype", "HPOId": "HP:0000001"}}
                ]}}
            return None

        with patch.object(provider, "_code_json", side_effect=code), \
                patch.object(provider, "_data_results", side_effect=data) as fetch:
            edges = provider.query(Node("Example disease", "ORPHA:1", "disease"))
        self.assertEqual([(e.relation, e.dst.id) for e in edges],
                         [("has_phenotype", "HP:0000001")])
        self.assertTrue(any("rd-associated-genes/" in c.args[0] for c in fetch.call_args_list))
