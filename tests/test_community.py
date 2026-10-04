import json
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from garra.community import CommunityCatalog
from garra.ui.server import create_server

FIXTURE = Path(__file__).resolve().parents[1] / "examples/communities.demo.json"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads(FIXTURE.read_text())

    def test_empty_catalog_keeps_three_sections(self):
        result = CommunityCatalog().list()
        self.assertEqual(
            [s["kind"] for s in result["sections"]], ["disease", "mechanism", "project"]
        )
        self.assertTrue(all(s["count"] == 0 for s in result["sections"]))
        self.assertFalse(result["demo"])
        self.assertFalse(result["capabilities"]["join"])

    def test_exact_filter_and_separate_kinds(self):
        catalog = CommunityCatalog(self.document)
        result = catalog.list(disease_id="MONDO:0009290")
        self.assertEqual([s["count"] for s in result["sections"]], [1, 0, 1])
        self.assertEqual(
            result["sections"][0]["items"][0]["match_reasons"],
            [{"field": "disease_ids", "id": "MONDO:0009290"}],
        )
        result = catalog.list(process_id="GO:0005980", kind="mechanism")
        self.assertEqual(len(result["sections"]), 1)
        self.assertEqual(result["sections"][0]["count"], 1)
        self.assertTrue(
            all(s["count"] == 0 for s in catalog.list(disease_id="MONDO:unknown")["sections"])
        )

    def test_links_and_mutation_isolation(self):
        catalog = CommunityCatalog(self.document)
        self.document["records"][0]["name"] = "changed"
        result = catalog.get("demo-pompe")
        self.assertEqual(result["project_ids"], ["demo-project"])
        result["record"]["name"] = "changed"
        self.assertNotEqual(catalog.get("demo-pompe")["record"]["name"], "changed")
        self.assertIsNone(catalog.get("missing"))

    def test_invalid_records_fail_at_startup(self):
        changes = [
            (0, "kind", "unknown"),
            (0, "kind", []),
            (0, "id", "../private"),
            (0, "disease_ids", ["Pompe"]),
            (0, "disease_ids", []),
            (0, "member_emails", ["private@example.org"]),
            (1, "process_ids", []),
            (1, "id", "demo-pompe"),
            (2, "community_ids", ["missing"]),
            (2, "community_ids", ["demo-project"]),
            (2, "owner", ""),
            (2, "reuse_checks", []),
            (2, "status", "unknown"),
            (2, "asset_urls", ["javascript:alert(1)"]),
        ]
        for index, field, value in changes:
            with self.subTest(field=field, value=value):
                doc = json.loads(FIXTURE.read_text())
                doc["records"][index][field] = value
                with self.assertRaises(ValueError):
                    CommunityCatalog(doc)

    def test_evidence_is_linked_not_promoted(self):
        evidence = dict(
            claim_id="claim-1",
            publication_id="PMID:32745073",
            url="https://pubmed.ncbi.nlm.nih.gov/32745073/",
            review_status="unreviewed",
        )
        self.document["records"][1]["evidence"] = [evidence]
        self.assertEqual(
            CommunityCatalog(self.document).get("demo-process")["record"]["evidence"], [evidence]
        )
        evidence["review_status"] = "verified"
        with self.assertRaises(ValueError):
            CommunityCatalog(self.document)


class CommunityHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server(
            port=0, communities=CommunityCatalog(json.loads(FIXTURE.read_text()))
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def fetch(self, path, origin="http://localhost:3000", method="GET"):
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}{path}",
            headers={"Origin": origin},
            method=method,
        )
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_routes(self):
        status, result = self.fetch("/api/communities?kind=mechanism&process_id=GO%3A0005980")
        self.assertEqual(status, 200)
        self.assertEqual(result["sections"][0]["count"], 1)
        self.assertTrue(result["demo"])
        self.assertEqual(self.fetch("/api/communities/demo-project")[0], 200)
        self.assertEqual(self.fetch("/api/communities/missing")[0], 404)
        self.assertEqual(self.fetch("/api/communities", origin="https://other.example")[0], 403)
        self.assertEqual(self.fetch("/api/communities", method="POST")[0], 404)

    def test_bad_filters(self):
        for query in (
            "kind=wrong",
            "kind=",
            "kind=disease&kind=project",
            "patient_id=private",
            "disease_id=Pompe",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.fetch("/api/communities?" + query)[0], 400)
