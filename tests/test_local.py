"""Local data layer (garra.local): query translation, router fallback, and an OBO ->
SQLite -> OLS-shaped response round trip on a tiny fixture. No downloads needed."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import garra.local as local
from garra.local import build_ontology, build_pubmed, router
from garra.local import pubmed as P

OBO = """format-version: 1.2

[Term]
id: MONDO:0000001
name: disease

[Term]
id: MONDO:0007947
name: Marfan syndrome
def: "A disorder of the connective tissue." [PMID:123, url:https\\://example.org/mfs]
synonym: "MFS" EXACT ABBREVIATION [OMIM:154700]
synonym: "Marfan's syndrome" EXACT []
xref: OMIM:154700
xref: Orphanet:558
is_a: MONDO:0000001 ! disease
relationship: has_material_basis_in_germline_mutation_in http://identifiers.org/hgnc/3603 ! FBN1

[Typedef]
id: has_material_basis_in_germline_mutation_in
name: has material basis in germline mutation in
"""


class QueryTranslationTests(unittest.TestCase):
    def setUp(self):
        p = patch("garra.local.pubmed.available", return_value=False)  # no MeSH explosion
        p.start()
        self.addCleanup(p.stop)

    def test_fields_and_booleans(self):
        q = P.to_fts('("Marfan Syndrome"[MeSH Major Topic] OR "Marfan syndrome"[tiab]) '
                     'AND review[pt]')
        self.assertEqual(q, '( {majr} : ("Marfan Syndrome") OR {tiab} : "Marfan syndrome" ) '
                            'AND {pt} : "review"')

    def test_heading_with_qualifier_and_implicit_and(self):
        self.assertEqual(P.to_fts('"Marfan Syndrome/genetics"[MeSH Terms]'),
                         '{mesh} : ("Marfan Syndrome genetics")')
        self.assertEqual(P.to_fts("Pompe disease"),
                         '{tiab mesh supp} : "Pompe" AND {tiab mesh supp} : "disease"')

    def test_unknown_field_is_left_to_the_api(self):
        with self.assertRaises(P.Unsupported):
            P.to_fts('"2020"[dp]')


class PubmedSearchTests(unittest.TestCase):
    """search() ranks with pubmed_year.bin exactly as the join on paper.year does."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for p in (patch.object(local, "LOCAL_DIR", root),
                  patch.object(build_pubmed, "YEARS", root / "pubmed_year.bin"),
                  patch.object(P, "YEARS", root / "pubmed_year.bin"),
                  patch.object(P, "_years", None),
                  patch("garra.local.pubmed.available", return_value=False)):
            p.start()
            self.addCleanup(p.stop)
        local.close_all()
        self.addCleanup(local.close_all)
        P.search.cache_clear()
        self.addCleanup(P.search.cache_clear)
        con = local.writer("pubmed")
        con.executescript("""
        CREATE TABLE paper(pmid INTEGER PRIMARY KEY, year INTEGER, journal TEXT, doc BLOB);
        CREATE VIRTUAL TABLE paper_fts USING fts5(tiab, mesh, majr, pt, supp, content='',
            contentless_delete=1);
        CREATE INDEX paper_year ON paper(year);
        """)
        docs = [(11, 1990, "Marfan syndrome in children", "Marfan Syndrome"),
                (12, 2021, "Marfan syndrome and FBN1", ""),
                (13, None, "A Marfan syndrome case", ""),
                (14, 2021, "Loeys-Dietz syndrome", "Marfan Syndrome"),
                (15, 2005, "Marfan syndrome Marfan syndrome review", "")]
        for pmid, year, tiab, mesh in docs:
            con.execute("INSERT INTO paper VALUES (?,?,?,?)", (pmid, year, "", b""))
            con.execute("INSERT INTO paper_fts(rowid, tiab, mesh, majr, pt, supp) "
                        "VALUES (?,?,?,?,?,?)", (pmid, tiab, mesh, "", "", ""))
        local.finish("pubmed", con)

    def test_year_file_matches_join(self):
        q = '"Marfan syndrome"[tiab] OR "Marfan Syndrome"[MeSH Terms]'
        expected = {s: P._search_join(P.db(), P.to_fts(q), 3, s)
                    for s in ("relevance", "date")}
        self.assertIsNone(P._year_map())  # no year file yet
        build_pubmed.write_years()
        self.assertIsNotNone(P._year_map())
        for s, want in expected.items():
            self.assertEqual(P.search(q, 3, s), want, s)
        self.assertEqual(P.search(q, 10, "date"), (5, ["14", "12", "15", "11", "13"]))


class RouterTests(unittest.TestCase):
    def test_disabled_or_missing_index_falls_back(self):
        url = "https://www.ebi.ac.uk/ols4/api/search?q=x&ontology=mondo"
        with patch.dict(os.environ, {"GARRA_LOCAL": "0"}):
            self.assertIsNone(router.route("GET", url))
        with patch("garra.local.router.available", return_value=False):
            self.assertIsNone(router.route("GET", url))
            self.assertFalse(router.serves(url))

    def test_unknown_host_is_not_served(self):
        self.assertIsNone(router.route("GET", "https://example.org/anything"))


class OntologyRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for p in (patch.object(local, "LOCAL_DIR", root),
                  patch.object(build_ontology, "OBO_FILES", {"mondo": "mondo.obo"}),
                  patch.object(build_ontology, "RAW", root)):
            p.start()
            self.addCleanup(p.stop)
        local.close_all()
        self.addCleanup(local.close_all)
        (root / "ontology").mkdir()
        (root / "ontology" / "mondo.obo").write_text(OBO, encoding="utf-8")
        (root / "ontology" / "hgnc_complete_set.txt").write_text(
            "hgnc_id\tsymbol\tname\tentrez_id\tensembl_gene_id\tuniprot_ids\tomim_id\t"
            "alias_symbol\tprev_symbol\tlocus_group\tstatus\n"
            "HGNC:3603\tFBN1\tfibrillin 1\t2200\tENSG00000166147\tP35555\t134797\t"
            "FBN\t\tprotein-coding gene\tApproved\n", encoding="utf-8")
        build_ontology.build()

    def get(self, url, **params):
        reply = router.route("GET", url, params=params)
        self.assertIsNotNone(reply, url)
        return json.loads(reply.bytes())

    def test_search_term_and_links(self):
        base = "https://www.ebi.ac.uk/ols4/api"
        docs = self.get(f"{base}/search", q="marfan's syndrome", ontology="mondo",
                        exact="true")["response"]["docs"]
        self.assertEqual(docs[0]["obo_id"], "MONDO:0007947")
        iri = "http%253A%252F%252Fpurl.obolibrary.org%252Fobo%252FMONDO_0007947"
        term = self.get(f"{base}/ontologies/mondo/terms/{iri}")
        self.assertIn("has_material_basis_in_germline_mutation_in", term["_links"])
        self.assertEqual(term["obo_definition_citation"][0]["oboXrefs"][1]["url"],
                         "https://example.org/mfs")
        abbr = [s for s in term["obo_synonym"] if s["type"] == "abbreviation"]
        self.assertEqual([s["name"] for s in abbr], ["MFS"])
        genes = self.get(f"{base}/ontologies/mondo/terms/{iri}/"
                         "has_material_basis_in_germline_mutation_in")["_embedded"]["terms"]
        self.assertEqual((genes[0]["obo_id"], genes[0]["label"]), ("HGNC:3603", "FBN1"))
        parents = self.get(f"{base}/ontologies/mondo/terms/{iri}/parents")
        self.assertEqual(parents["_embedded"]["terms"][0]["obo_id"], "MONDO:0000001")

    def test_hgnc_fetch(self):
        d = self.get("https://rest.genenames.org/fetch/alias_symbol/FBN")
        self.assertEqual(d["response"]["docs"][0]["symbol"], "FBN1")


if __name__ == "__main__":
    unittest.main()
