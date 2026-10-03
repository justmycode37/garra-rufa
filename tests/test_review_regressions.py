"""Regression cases found during the pre-push review; no live data required."""

import json
import tempfile
import threading
import unittest
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from garra.actions.journey import _search_terms, build_journey
from garra.connections import build_connections
from garra.graph.resolve import resolve_query
from garra.ingest.loaders import init_db, load_hpo_disease_phenotype, upsert_disease
from garra.similarity import _confidence, similar_diseases
from garra.ui.monarch import MonarchClient, UpstreamError, normalize_results
from garra.ui.server import create_server
from garra.ui.service import ConnectionService, InputError, validate_request

ROOT = Path(__file__).resolve().parents[1]


class ReviewRegressions(unittest.TestCase):
    def packet(self):
        return json.loads((ROOT / "examples/connections-demo.json").read_text())

    def test_invalid_packet_objects_raise_validation_error(self):
        for body in [None, [], {"schema_version": True}, {"schema_version": 1, "anchor": None}]:
            with self.subTest(body=body), self.assertRaises(ValueError):
                build_connections(body)
        packet = self.packet()
        packet["candidates"][0]["similarity"] = None
        with self.assertRaises(ValueError):
            build_connections(packet)

    def test_invalid_enums_and_boolean_are_validation_errors(self):
        for field, value in [("relationship", []), ("assessment", {}), ("reviewed", 1)]:
            packet = self.packet()
            packet["candidates"][0]["claims"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_connections(packet)
        packet = self.packet()
        packet["candidates"][0]["coverage"] = {"pathway": []}
        with self.assertRaises(ValueError):
            build_connections(packet)

    def test_claim_asset_ids_cannot_collide(self):
        packet = self.packet()
        packet["candidates"][0]["assets"][0]["id"] = "C1"
        with self.assertRaises(ValueError):
            build_connections(packet)

    def test_huge_numbers_are_invalid_not_server_errors(self):
        with self.assertRaises(InputError):
            validate_request({"hpo_ids": ["HP:0001324"], "confirmed": True, "min_score": 10**1000})
        packet = self.packet()
        packet["candidates"][0]["similarity"]["value"] = 10**1000
        with self.assertRaises(ValueError):
            build_connections(packet)

    def test_invalid_enrichment_fails_at_startup(self):
        for catalog in [
            [],
            {},
            {"schema_version": 1, "diseases": [None]},
            {"schema_version": 1, "mappings": {"MONDO:1": []}},
        ]:
            with self.subTest(catalog=catalog), self.assertRaises(ValueError):
                ConnectionService(enrichment=catalog)

    def test_interrupted_upstream_maps_to_retryable_failure(self):
        def fail(*args, **kwargs):
            raise IncompleteRead(b"partial")

        with self.assertRaises(UpstreamError):
            MonarchClient(opener=fail).search(["HP:0001324"], "jaccard_similarity")

    def test_incomplete_matches_and_bad_ancestors_are_rejected(self):
        fixture = ROOT / "tests/fixtures/monarch-search.json"
        raw = json.loads(fixture.read_text())
        raw[0]["similarity"]["object_best_matches"] = {}
        with self.assertRaises(UpstreamError):
            normalize_results(raw, "jaccard_similarity")
        raw = json.loads(fixture.read_text())
        entry = next(iter(raw[0]["similarity"]["object_best_matches"].values()))
        entry["match_subsumer"] = float("nan")
        with self.assertRaises(UpstreamError):
            normalize_results(raw, "jaccard_similarity")

    def test_hpo_comment_header_negation_and_name_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "phenotype.hpoa"
            source.write_text(
                "#description: fixture\n#database_id\tdisease_name\tqualifier\thpo_id\tfrequency\n"
                "OMIM:1\tExample\t\tHP:0001324\t\n"
                "OMIM:1\tExample\tNOT\tHP:0001252\t\n"
            )
            conn = init_db(root / "atlas.sqlite")
            try:
                upsert_disease(conn, "OMIM:1", "Example disease")
                with patch("garra.ingest.loaders.raw_file", return_value=source):
                    report = load_hpo_disease_phenotype(conn)
                terms = {
                    row[0] for row in conn.execute("SELECT hpo_id FROM edge_disease_phenotype")
                }
                self.assertEqual(terms, {"HP:0001324"})
                self.assertEqual(report["excluded_rows"], 1)
                # A subsequent source revision negates the formerly positive term.
                source.write_text(
                    "database_id\tqualifier\thpo_id\tfrequency\n"
                    "OMIM:1\tNOT\tHP:0001324\t\n"
                    "OMIM:1\t\tHP:0001252\t0/19\n"
                )
                with patch("garra.ingest.loaders.raw_file", return_value=source):
                    load_hpo_disease_phenotype(conn)
                self.assertEqual(
                    conn.execute("SELECT count(*) FROM edge_disease_phenotype").fetchone()[0], 0
                )
                self.assertEqual(
                    conn.execute("SELECT primary_name FROM disease").fetchone()[0],
                    "Example disease",
                )
            finally:
                conn.close()

    def test_ambiguous_alias_does_not_pick_first_disease(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "atlas.sqlite"
            conn = init_db(db)
            try:
                upsert_disease(conn, "OMIM:1", "First", aliases=["shared name"])
                upsert_disease(conn, "OMIM:2", "Second", aliases=["shared name"])
                conn.commit()
            finally:
                conn.close()
            result = resolve_query("shared name", db_path=db)
            self.assertEqual(result["status"], "ambiguous")
            self.assertNotIn("primary", result)
            self.assertEqual(resolve_query("OMIM:1", db_path=db)["status"], "ok")

    def test_gene_ambiguity_stops_journey_before_live_queries(self):
        store = MagicMock()
        store.diseases_for_gene.return_value = [
            {"disease_key": "OMIM:1", "primary_name": "First"},
            {"disease_key": "OMIM:2", "primary_name": "Second"},
        ]
        with patch("garra.similarity.AtlasStore", return_value=store):
            result = similar_diseases(gene_key="SYMBOL:TEST")
        self.assertEqual(result["status"], "ambiguous")
        resolved = {"status": "ok", "primary": {"kind": "gene", "gene_key": "SYMBOL:TEST"}}
        with (
            patch("garra.actions.journey.resolve_query", return_value=resolved),
            patch("garra.actions.journey.similar_diseases", return_value=result),
            patch("garra.actions.journey.search_clinicaltrials") as search,
        ):
            self.assertEqual(build_journey("TEST")["status"], "ambiguous")
            search.assert_not_called()

    def test_search_term_order_is_stable(self):
        resolved = {"primary": {"kind": "disease", "name": "Canonical"}, "matches": []}
        self.assertEqual(_search_terms("Alias", resolved), ["Alias", "Canonical"])

    def test_legacy_overlap_does_not_claim_high_confidence(self):
        self.assertNotEqual(_confidence(0.45, has_pathway=True, has_phenotype=False), "high")

    def test_http_upstream_failures_and_internal_errors_are_distinct(self):
        class FailingService:
            error = None

            def search(self, body):
                raise self.error

        service = FailingService()
        server = create_server(port=0, service=service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cases = [
                (UpstreamError("offline"), 502),
                (UpstreamError("timeout", timeout=True), 504),
                (InputError("bad input"), 400),
                (ValueError("internal catalog bug"), 500),
            ]
            for error, code in cases:
                service.error = error
                req = Request(
                    f"http://127.0.0.1:{server.server_port}/api/connections/search",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                )
                with self.subTest(code=code), self.assertRaises(HTTPError) as caught:
                    urlopen(req, timeout=3)
                with caught.exception as response:
                    self.assertEqual(response.code, code)
                    self.assertIn("error", json.load(response))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
