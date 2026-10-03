"""Checks that the registry and the downloader refuse bad files."""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

from garra.sources.catalog import (
    BULK_SOURCES,
    MANUAL_SOURCES,
    QUERY_SOURCES,
    validate_catalog,
)
from garra.sources.client import fetch_bulk
from garra.sources.queries import parse_clinicaltrials, parse_pubmed_summary, parse_reporter


class _Body(io.BytesIO):
    def __init__(self, data: bytes, url: str, status: int = 200, content_type: str = "text/plain"):
        super().__init__(data)
        self.url = url
        self.status = status
        self.headers = {"Content-Type": content_type}

    def geturl(self) -> str:
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class CatalogTests(unittest.TestCase):
    def test_registry_is_internally_consistent(self):
        validate_catalog()

    def test_manual_sources_are_not_bulk_files(self):
        self.assertIn("orphanet_patient_orgs", MANUAL_SOURCES)
        self.assertTrue(all(source.access == "manual" for source in MANUAL_SOURCES.values()))
        self.assertNotIn("orphanet_patient_orgs", BULK_SOURCES)

    def test_variant_summary_is_not_a_default_download(self):
        source = BULK_SOURCES["clinvar_variant_summary"]
        self.assertFalse(source.default)
        self.assertGreater(source.max_bytes, 100 * 1024 * 1024)

    def test_query_hosts_match_the_documented_services(self):
        self.assertEqual(QUERY_SOURCES["clinicaltrials"].hosts, ("clinicaltrials.gov",))
        self.assertEqual(QUERY_SOURCES["nih_reporter"].hosts, ("api.reporter.nih.gov",))
        self.assertEqual(QUERY_SOURCES["pubmed"].hosts, ("eutils.ncbi.nlm.nih.gov",))


class FetchTests(unittest.TestCase):
    def test_small_file_is_rejected_as_a_stub(self):
        source = BULK_SOURCES["hpo_genes_to_disease"]
        body = _Body(b"ncbi_gene_id\tshort\n", source.url)

        def opener(_handler):
            class _Opener:
                def open(self, request, timeout=0):
                    return body

            return _Opener()

        with TemporaryDirectory() as tmp, patch("garra.sources.client.build_opener", opener):
            record = fetch_bulk(source, Path(tmp))
        self.assertEqual(record["status"], "rejected")
        self.assertIn("empty stub", record["message"])
        self.assertFalse((Path(tmp) / source.id / source.filename).exists())

    def test_wrong_marker_is_rejected(self):
        source = BULK_SOURCES["orphadata_genes"]
        body = _Body(b"<html>not the xml</html>" + b"x" * 2_000_000, source.url)

        def opener(_handler):
            class _Opener:
                def open(self, request, timeout=0):
                    return body

            return _Opener()

        with TemporaryDirectory() as tmp, patch("garra.sources.client.build_opener", opener):
            record = fetch_bulk(source, Path(tmp))
        self.assertEqual(record["status"], "rejected")
        self.assertIn("expected marker", record["message"])

    def test_valid_file_is_saved_with_a_hash(self):
        source = BULK_SOURCES["clinvar_gene_condition"]
        payload = b"#GeneID\tAssociatedGenes\n" + b"1\tGENE\n" * 20_000
        body = _Body(payload, source.url)

        def opener(_handler):
            class _Opener:
                def open(self, request, timeout=0):
                    return body

            return _Opener()

        with TemporaryDirectory() as tmp, patch("garra.sources.client.build_opener", opener):
            record = fetch_bulk(source, Path(tmp))
            saved = Path(tmp) / source.id / source.filename
            self.assertEqual(record["status"], "ok")
            self.assertEqual(saved.read_bytes(), payload)
            index = json.loads((Path(tmp) / "manifest.json").read_text())
        self.assertEqual(index["sources"]["clinvar_gene_condition"]["sha256"], record["sha256"])
        self.assertEqual(record["source_url"], source.url)

    def test_http_404_is_not_found(self):
        source = BULK_SOURCES["clinvar_gene_condition"]

        def opener(_handler):
            class _Opener:
                def open(self, request, timeout=0):
                    raise HTTPError(source.url, 404, "missing", hdrs=None, fp=None)

            return _Opener()

        with TemporaryDirectory() as tmp, patch("garra.sources.client.build_opener", opener):
            record = fetch_bulk(source, Path(tmp))
        self.assertEqual(record["status"], "not_found")

    def test_large_clinvar_file_needs_an_explicit_flag(self):
        source = BULK_SOURCES["clinvar_variant_summary"]
        with TemporaryDirectory() as tmp:
            record = fetch_bulk(source, Path(tmp), allow_large=False)
        self.assertEqual(record["status"], "rejected")
        self.assertIn("allow_large", record["message"])

    def test_manual_source_is_not_downloaded(self):
        source = MANUAL_SOURCES["nord"]
        with TemporaryDirectory() as tmp:
            record = fetch_bulk(source, Path(tmp))
        self.assertEqual(record["status"], "rejected")


class QueryParseTests(unittest.TestCase):
    def test_clinicaltrials_keeps_the_study_link(self):
        records = parse_clinicaltrials(
            {
                "studies": [
                    {
                        "protocolSection": {
                            "identificationModule": {"nctId": "NCT00000001", "briefTitle": "A study"},
                            "statusModule": {"overallStatus": "RECRUITING"},
                            "conditionsModule": {"conditions": ["Pompe disease"]},
                            "designModule": {"studyType": "OBSERVATIONAL", "phases": []},
                        }
                    }
                ]
            }
        )
        self.assertEqual(records[0]["url"], "https://clinicaltrials.gov/study/NCT00000001")
        self.assertEqual(records[0]["conditions"], ["Pompe disease"])

    def test_reporter_keeps_pi_and_project_url(self):
        records = parse_reporter(
            {
                "results": [
                    {
                        "project_num": "5R00HL161420-05",
                        "project_title": "Novel Adjunctive Therapies for Pompe Disease",
                        "contact_pi_name": "ROGER, ANGELA L.",
                        "fiscal_year": 2026,
                        "agency_ic_admin": {"abbreviation": "NHLBI"},
                        "organization": {"org_name": "DUKE UNIVERSITY"},
                        "project_detail_url": "https://reporter.nih.gov/project-details/11263644",
                        "abstract_text": "x" * 500,
                    }
                ]
            }
        )
        self.assertEqual(records[0]["pi"], "ROGER, ANGELA L.")
        self.assertEqual(len(records[0]["abstract_excerpt"]), 400)

    def test_pubmed_summary_keeps_the_pmid_link(self):
        records = parse_pubmed_summary(
            {
                "result": {
                    "uids": ["42771559"],
                    "42771559": {
                        "uid": "42771559",
                        "title": "A paper",
                        "fulljournalname": "Some Journal",
                        "pubdate": "2026",
                        "authors": [{"name": "Ada Lovelace"}],
                    },
                }
            }
        )
        self.assertEqual(records[0]["url"], "https://pubmed.ncbi.nlm.nih.gov/42771559/")


if __name__ == "__main__":
    unittest.main()
