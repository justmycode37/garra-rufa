import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from garra.discovery import DiscoveryEngine
from garra.ingest.schema import SCHEMA
from garra.ui.server import create_server
from garra.ui.service import InputError


def snapshot():
    nodes = [
        {"id": x, "labels": [label], "kind": kind, "identity_verified": True}
        for x, label, kind in [
            ("MONDO:1", "Alpha", "disease"),
            ("MONDO:2", "Beta", "disease"),
            ("MONDO:3", "Gamma", "disease"),
            ("GO:1", "Process one", "process"),
            ("GO:2", "Process two", "process"),
        ]
    ]

    def pair(a, b, score, processes):
        return {
            "disease_a": a,
            "disease_b": b,
            "phenotype_similarity": {"metric": "exact_hpo_jaccard", "value": score},
            "shared_process_ids": processes,
            "candidate_claim_ids": ["c1"],
            "assessment": {
                "status": "conflicting_evidence",
                "supporting_claim_ids": [],
                "contradicting_claim_ids": ["c1"],
                "context_unknown": ["tissue"],
            },
        }

    return {
        "schema_version": 1,
        "nodes": nodes,
        "candidates": [
            pair("MONDO:1", "MONDO:2", 0.8, ["GO:1"]),
            pair("MONDO:2", "MONDO:3", 0.7, ["GO:1", "GO:2"]),
            pair("MONDO:1", "MONDO:3", 0.1, ["GO:1"]),
        ],
        "claims": [{"id": "c1", "paper_id": "PMID:1", "reviewed": False}],
        "papers": [{"id": "PMID:1", "identifiers": ["PMID:1"]}],
    }


class DiscoveryTests(unittest.TestCase):
    def test_overlapping_pathways_and_no_transitive_phenotype_cluster(self):
        engine = DiscoveryEngine(snapshot())
        groups = engine.clusters()["clusters"]
        self.assertEqual(len(groups), 3)
        phenotype = [g for g in groups if g["kind"] == "phenotype"]
        self.assertEqual(phenotype[0]["member_ids"], ["MONDO:1", "MONDO:2"])
        self.assertEqual(len(engine.memberships["MONDO:2"]), 3)
        pathway = next(g for g in groups if g["process_id"] == "GO:1")
        detail = engine.cluster(pathway["id"])
        self.assertEqual(len(detail["cluster"]["member_ids"]), 3)
        self.assertEqual(
            detail["cluster"]["pairs"][0]["assessment"]["status"], "conflicting_evidence"
        )
        self.assertEqual(detail["claims"][0]["id"], "c1")
        self.assertEqual(detail["papers"][0]["id"], "PMID:1")
        detail["claims"].clear()
        self.assertTrue(engine.cluster(pathway["id"])["claims"])

    def test_missing_phenotypes_do_not_block_pathways(self):
        doc = snapshot()
        for pair in doc["candidates"]:
            pair["phenotype_similarity"]["value"] = None
        engine = DiscoveryEngine(doc)
        self.assertEqual(engine.clusters(kind="phenotype")["total"], 0)
        self.assertEqual(engine.clusters(kind="pathway")["total"], 2)

    def test_native_metric_cannot_use_jaccard_threshold(self):
        doc = snapshot()
        for pair in doc["candidates"]:
            pair["phenotype_similarity"]["metric"] = "native_metric"
        self.assertEqual(DiscoveryEngine(doc).clusters(kind="phenotype")["total"], 0)

    def test_deterministic_and_threshold_sensitive(self):
        doc = snapshot()
        engine = DiscoveryEngine(doc)
        doc["nodes"].reverse()
        doc["candidates"].reverse()
        self.assertEqual(engine.clusters()["clusters"], DiscoveryEngine(doc).clusters()["clusters"])
        self.assertEqual(
            DiscoveryEngine(doc, phenotype_threshold=0.9).clusters(kind="phenotype")["total"], 0
        )

    def test_search_exact_ids_labels_filters_pagination(self):
        engine = DiscoveryEngine(snapshot())
        self.assertEqual(engine.search({"query": "mondo:1"})["results"][0]["match"], "exact_id")
        self.assertEqual(engine.search({"query": " ALPHA "})["results"][0]["id"], "MONDO:1")
        self.assertEqual(engine.search({"query": "Process", "kind": "disease"})["total"], 0)
        data = engine.search({"query": "Process", "limit": 1, "offset": 1})
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["results"][0]["id"], "GO:2")
        self.assertEqual(engine.clusters(entity_id="MONDO:missing")["total"], 0)
        self.assertIsNone(engine.cluster("unknown"))

    def test_empty_and_invalid(self):
        self.assertEqual(DiscoveryEngine().search({"query": "nothing"})["total"], 0)
        for body in (
            [],
            {},
            {"query": ""},
            {"query": "x", "limit": True},
            {"query": "x", "offset": -1},
            {"query": "x", "kind": []},
            {"query": "x", "patient_name": "private"},
        ):
            with self.subTest(body=body), self.assertRaises(InputError):
                DiscoveryEngine().search(body)
        for threshold in (0, -1, 2, float("nan"), True):
            with self.assertRaises(ValueError):
                DiscoveryEngine(phenotype_threshold=threshold)
        for change in ("claims", "papers"):
            doc = snapshot()
            doc[change] = []
            with self.assertRaises(ValueError):
                DiscoveryEngine(doc)

    def test_atlas_aliases_do_not_merge_identity_or_invent_clusters(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "atlas.sqlite"
            conn = sqlite3.connect(path)
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO disease VALUES ('OMIM:1', 'Alpha alias disease', 'OMIM', '1')"
            )
            conn.execute("INSERT INTO disease_alias VALUES ('alpha', 'OMIM:1')")
            conn.commit()
            conn.close()
            engine = DiscoveryEngine(snapshot(), atlas=path)
            self.assertTrue(engine.search({"query": "alpha"})["ambiguous_exact_match"])
            results = engine.search({"query": "alpha"})["results"]
            self.assertEqual({r["id"] for r in results}, {"MONDO:1", "OMIM:1"})
            alias = next(r for r in results if r["id"] == "OMIM:1")
            self.assertFalse(alias["bridge_available"])
            self.assertFalse(alias["cluster_ids"])


class DiscoveryHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Symptoms:
            def search(self, body):
                if body.get("confirmed") is not True:
                    raise InputError("Confirm symptoms")
                return {"cards": [{"id": "MONDO:1"}]}

        cls.server = create_server(
            port=0, discovery=DiscoveryEngine(snapshot()), service=Symptoms()
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def fetch(self, path, body=None, origin="http://localhost:3000"):
        req = Request(
            f"http://127.0.0.1:{self.server.server_port}" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "Origin": origin},
        )
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            return response.status, json.loads(
                raw
            ) if response.headers.get_content_type() == "application/json" else raw.decode()

    def test_search_to_cluster_to_evidence(self):
        status, data = self.fetch("/api/search", {"query": "Alpha"})
        self.assertEqual(status, 200)
        self.assertEqual(
            self.fetch(
                "/api/search",
                {"query": "Alpha"},
                origin=f"http://127.0.0.1:{self.server.server_port}",
            )[0],
            200,
        )
        cluster = data["results"][0]["cluster_ids"][0]
        status, detail = self.fetch("/api/clusters/" + cluster)
        self.assertEqual(status, 200)
        self.assertTrue(detail["claims"])
        self.assertEqual(self.fetch("/api/clusters?entity_id=MONDO%3A1")[1]["total"], 2)
        self.assertEqual(self.fetch("/explore")[0], 200)

    def test_phenotype_search_and_errors(self):
        status, data = self.fetch("/api/search", {"mode": "phenotype", "confirmed": True})
        self.assertEqual(status, 200)
        self.assertTrue(data["cards"][0]["cluster_ids"])
        self.assertEqual(self.fetch("/api/search", {"mode": "phenotype"})[0], 400)
        self.assertEqual(self.fetch("/api/search", {"query": ""})[0], 400)
        self.assertEqual(
            self.fetch("/api/search", {"query": "x"}, origin="https://other.example")[0], 403
        )
        for path in (
            "/api/clusters?kind=wrong",
            "/api/clusters?kind=pathway&kind=phenotype",
            "/api/clusters?private=x",
        ):
            self.assertEqual(self.fetch(path)[0], 400)
        self.assertEqual(self.fetch("/api/clusters/missing")[0], 404)
