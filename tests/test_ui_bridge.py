import io
import json
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from garra.ui.catalog import IDS, get_catalog
from garra.ui.monarch import MonarchClient, UpstreamError, normalize_results
from garra.ui.server import create_server
from garra.ui.service import ConnectionService, InputError, validate_request

FIXTURE = Path(__file__).parent / "fixtures" / "monarch-search.json"
REQUEST = {"hpo_ids": ["HP:0001324", "HP:0001252"], "confirmed": True}


def response():
    return json.loads(FIXTURE.read_text())


class BridgeTests(unittest.TestCase):
    def client(self, raw=None, **kwargs):
        raw = response() if raw is None else raw
        self.calls = []

        def opener(req, timeout):
            self.calls.append(json.loads(req.data))
            return io.BytesIO(json.dumps(raw).encode())

        return MonarchClient(opener=opener, **kwargs)

    def test_catalog_terms_and_region_references(self):
        catalog = get_catalog()
        regions = {r["id"] for r in catalog["regions"]}
        self.assertEqual(len(IDS), 11)
        for s in catalog["symptoms"]:
            self.assertTrue(set(s["regions"]) <= regions)
        catalog["symptoms"].clear()
        self.assertEqual(len(get_catalog()["symptoms"]), 11)

    def test_body_click_is_not_search(self):
        for body in [
            {"regions": ["legs"], "confirmed": True},
            {"hpo_ids": ["HP:0001324"]},
            {**REQUEST, "hpo_ids": ["HP:9999999"]},
            {**REQUEST, "hpo_ids": []},
            {**REQUEST, "confirmed": 1},
            {**REQUEST, "patient_name": "Test"},
        ]:
            with self.subTest(body=body), self.assertRaises(InputError):
                validate_request(body)

    def test_validation_numeric_edges(self):
        for key, value in [
            ("limit", True),
            ("limit", 26),
            ("min_score", float("nan")),
            ("min_score", 101),
            ("metric", []),
        ]:
            with self.subTest(key=key, value=value), self.assertRaises(InputError):
                validate_request({**REQUEST, key: value})

    def test_native_metric_rejects_percentage_filter(self):
        with self.assertRaises(InputError):
            validate_request({**REQUEST, "metric": "ancestor_information_content", "min_score": 50})
        self.assertIsNone(
            validate_request({**REQUEST, "metric": "ancestor_information_content"})[2]
        )

    def test_live_fixture_parsing_and_orientation(self):
        out = normalize_results(response(), "jaccard_similarity")
        self.assertEqual(out[0]["value"], 0.95)
        self.assertEqual(out[0]["disease_annotation_count"], 1)
        self.assertEqual(out[0]["query_term_count"], 2)
        match = next(m for m in out[0]["matched_phenotypes"] if m["query_id"] == "HP:0001252")
        self.assertEqual(match["disease_phenotype_id"], "HP:0001324")
        self.assertFalse(match["exact"])

    def test_schema_changes_fail_explicitly(self):
        raw = response()
        raw[0]["similarity"]["metric"] = "phenodigm_score"
        with self.assertRaises(UpstreamError):
            normalize_results(raw, "jaccard_similarity")
        with self.assertRaises(UpstreamError):
            normalize_results({"results": []}, "jaccard_similarity")

    def test_cache_normalization_expiry_and_copy(self):
        now = [0]
        client = self.client(clock=lambda: now[0], ttl=10, capacity=1)
        out = client.search(REQUEST["hpo_ids"], "jaccard_similarity")
        out["results"].clear()
        cached = client.search(list(reversed(REQUEST["hpo_ids"])), "jaccard_similarity")
        self.assertTrue(cached["cache"]["hit"])
        self.assertTrue(cached["results"])
        self.assertEqual(len(self.calls), 1)
        now[0] = 11
        self.assertFalse(client.search(REQUEST["hpo_ids"], "jaccard_similarity")["cache"]["hit"])
        self.assertEqual(len(self.calls), 2)

    def test_wrong_query_terms_rejected(self):
        with self.assertRaises(UpstreamError):
            self.client().search(["HP:0012378"], "jaccard_similarity")

    def test_timeout_and_error_not_cached(self):
        for exc in [
            TimeoutError(),
            URLError("offline"),
            HTTPError("https://example.org", 429, "rate limit", None, None),
        ]:

            def fail(*args, **kwargs):
                raise exc

            client = MonarchClient(opener=fail)
            with self.subTest(exc=exc), self.assertRaises(UpstreamError):
                client.search(REQUEST["hpo_ids"], "jaccard_similarity")
            self.assertFalse(client.cache)

    def test_score_cards_have_explicit_gaps(self):
        result = ConnectionService(client=self.client()).search(REQUEST)
        card = result["cards"][0]
        self.assertEqual(card["similarity"]["display_score"], 95)
        self.assertEqual(result["query"]["mode"], "symptom_profile")
        self.assertFalse(card["assets"])
        self.assertFalse(card["papers"])
        self.assertTrue(any("Sparse" in w for w in card["warnings"]))
        self.assertEqual(self.calls[0]["group"], "Human Diseases")
        self.assertEqual(self.calls[0]["limit"], 50)

    def test_no_results_and_threshold(self):
        self.assertEqual(
            ConnectionService(client=self.client([])).search(REQUEST)["status"], "no_matches"
        )
        result = ConnectionService(client=self.client()).search({**REQUEST, "min_score": 96})
        self.assertFalse(result["cards"])

    def test_verified_enrichment_join(self):
        catalog = {
            "schema_version": 1,
            "diseases": [
                {
                    "id": "ORPHA:example",
                    "name": "Research example",
                    "scope": "disease_research",
                    "claims": [
                        {
                            "id": "C1",
                            "relationship": "shared_pathway",
                            "statement": "Sourced disease research observation",
                            "assessment": "supported",
                            "reviewed": True,
                            "supporting_evidence_ids": ["E1"],
                        }
                    ],
                    "assets": [
                        {
                            "id": "A1",
                            "name": "Assay",
                            "kind": "assay",
                            "owner": "Lab",
                            "url": "https://example.org/asset",
                            "access_conditions": "Ask owner",
                            "claim_ids": ["C1"],
                            "evidence_ids": ["E1"],
                            "reuse_checks": ["Validate model compatibility"],
                        }
                    ],
                }
            ],
            "mappings": {"MONDO:0001540": "ORPHA:example"},
            "evidence": [
                {
                    "id": "E1",
                    "source": "Study",
                    "url": "https://example.org/study",
                    "retrieved_at": "2026-10-03",
                    "locator": "Figure 1",
                    "paper_ids": ["P1"],
                }
            ],
            "papers": [
                {
                    "id": "P1",
                    "title": "Study",
                    "url": "https://example.org/study",
                    "access": "unknown",
                }
            ],
        }
        out = ConnectionService(client=self.client(), enrichment=catalog).search(REQUEST)
        card = next(c for c in out["cards"] if c["id"] == "MONDO:0001540")
        self.assertEqual(card["enrichment_id"], "ORPHA:example")
        self.assertEqual(len(card["assets"]), 1)
        self.assertEqual(len(card["papers"]), 1)
        self.assertEqual(card["evidence_scope"], "disease_research_not_patient_mechanism")
        catalog["diseases"][0]["scope"] = "disease_pair"
        with self.assertRaises(ValueError):
            ConnectionService(enrichment=catalog)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        client = MonarchClient(opener=lambda *a, **k: io.BytesIO(FIXTURE.read_bytes()))
        cls.server = create_server(port=0, service=ConnectionService(client=client))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def send(self, path, body=None, origin="http://localhost:5173", method=None):
        req = Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Origin": origin, "Content-Type": "application/json"},
            method=method,
        )
        try:
            res = urlopen(req, timeout=5)
        except HTTPError as exc:
            res = exc
        with res:
            return res.status, dict(res.headers), json.load(res)

    def test_root_explains_routes(self):
        status, _, body = self.send("/")
        self.assertEqual(status, 200)
        self.assertEqual(body["routes"]["symptom_menu"], "/api/body-map")
        self.assertEqual(body["routes"]["search"]["method"], "POST")

    def test_catalog_and_cors(self):
        status, headers, body = self.send("/api/body-map")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://localhost:5173")
        self.assertEqual(len(body["symptoms"]), 11)

    def test_preflight(self):
        self.assertEqual(self.send("/api/connections/search", method="OPTIONS")[0], 200)

    def test_disallowed_origin(self):
        self.assertEqual(
            self.send("/api/connections/search", REQUEST, origin="https://unapproved.example")[0],
            403,
        )

    def test_search(self):
        status, _, body = self.send("/api/connections/search", REQUEST)
        self.assertEqual(status, 200)
        self.assertTrue(body["cards"])

    def test_invalid_input_and_routes(self):
        self.assertEqual(self.send("/api/connections/search", {"regions": ["legs"]})[0], 400)
        self.assertEqual(self.send("/absent")[0], 404)


if __name__ == "__main__":
    unittest.main()
