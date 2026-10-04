"""Hosted routing must expose the same discovery contract without bypassing auth."""
import gzip
import importlib.util
import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

from garra.discovery.hosted import FILES, prepare_bundle

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hosted_api", ROOT / "api/index.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class HostedRoutesTest(unittest.TestCase):
    def request(self, path, *, method="GET", token="test-token", body=None):
        request = object.__new__(api.handler)
        request.path = path
        request.headers = Message()
        request.headers["Authorization"] = "Bearer " + token
        raw = json.dumps(body).encode()
        request.headers["Content-Type"] = "application/json"
        request.headers["Content-Length"] = str(len(raw))
        request.rfile = io.BytesIO(raw)
        request.reply = Mock()
        with patch.dict(api.os.environ, {"GARRA_RESEARCH_TOKEN": "test-token"}):
            getattr(request, "do_" + method)()
        return request.reply.call_args.args

    def test_auth_is_required_for_both_methods(self):
        for path, method in [("/api/regions", "GET"), ("/api/search", "POST")]:
            with patch.object(api, "load_discovery") as load:
                self.assertEqual(self.request(path, method=method, token="wrong")[0], 401)
                load.assert_not_called()

    def test_missing_config_fails_closed(self):
        with patch.dict(api.os.environ, {"GARRA_RESEARCH_TOKEN": ""}):
            request = object.__new__(api.handler)
            request.headers = Message()
            self.assertFalse(request.authorized())

    def test_get_contracts(self):
        engine, regions = Mock(), Mock()
        for path, target in [
            ("/api/regions", regions.catalog),
            ("/api/regions/heart", regions.detail),
            ("/api/neighbors?entity_id=MONDO:1", regions.neighbors),
            ("/api/clusters?kind=pathway", engine.clusters),
            ("/api/clusters/example", engine.cluster),
            ("/api/entity?id=MONDO:1", engine.entity_graph),
        ]:
            target.return_value = {"test": path}
            with patch.object(api, "load_discovery", return_value=(engine, regions)):
                self.assertEqual(self.request(path), (200, {"test": path}))
        regions.detail.assert_called_with("heart")
        regions.neighbors.assert_called_with("MONDO:1")
        engine.clusters.assert_called_with(kind="pathway")
        engine.entity_graph.assert_called_with("MONDO:1")

    def test_communities_route_uses_public_catalog(self):
        engine, regions = Mock(), Mock()
        with patch.object(api, "load_discovery", return_value=(engine, regions)):
            status, result = self.request("/api/communities?disease_id=MONDO%3A0009290")
        self.assertEqual(status, 200)
        self.assertTrue(any(section["items"] for section in result["sections"]))

    def test_invalid_filters_and_unknown_routes(self):
        with patch.object(api, "load_discovery", return_value=(Mock(), Mock())):
            for path in ["/api/entity", "/api/entity?id=a&id=b", "/api/clusters?unexpected=1", "/api/regions?unexpected=1", "/api/neighbors", "/api/neighbors?entity_id=a&entity_id=b", "/api/communities?patient_id=private"]:
                self.assertEqual(self.request(path)[0], 400)
            self.assertEqual(self.request("/does-not-exist")[0], 404)
            self.assertEqual(self.request("/api/research/missing", method="POST")[0], 404)

    def test_missing_record_and_unavailable_bundle(self):
        engine = Mock()
        engine.cluster.return_value = None
        with patch.object(api, "load_discovery", return_value=(engine, Mock())):
            self.assertEqual(self.request("/api/clusters/missing")[0], 404)
        with patch.object(api, "load_discovery", side_effect=FileNotFoundError("private/path")):
            self.assertEqual(self.request("/health", token="")[0], 503)
            code, body = self.request("/api/regions")
            self.assertEqual(code, 503)
            self.assertNotIn("private", str(body))

    def test_post_search_and_regional_atlas_use_snapshot(self):
        engine, regions = Mock(), Mock()
        engine.search.return_value = {"results": []}
        regions.atlas.return_value = {"nodes": []}
        with patch.object(api, "load_discovery", return_value=(engine, regions)):
            self.assertEqual(self.request("/api/search", method="POST", body={"query": "heart"}), (200, {"results": []}))
            self.assertEqual(self.request("/api/research/atlas", method="POST", body={"region": "HP:1"}), (200, {"nodes": []}))
        engine.search.assert_called_once_with({"query": "heart"})
        regions.atlas.assert_called_once_with({"region": "HP:1"})


class BundleTest(unittest.TestCase):
    def test_rejects_corrupted_bundle_and_unexpected_files(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = {"schema_version": 1, "files": {}}
            for name in FILES:
                raw = b"fixture"
                (root / (name + ".gz")).write_bytes(gzip.compress(raw))
                manifest["files"][name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            (root / "manifest.json").write_text(json.dumps(manifest))
            prepare_bundle(root, root / "out")
            self.assertEqual((root / "out/bridge.json").read_bytes(), b"fixture")
            (root / "bridge.json.gz").write_bytes(gzip.compress(b"changed"))
            with self.assertRaises(ValueError):
                prepare_bundle(root, root / "out")
            manifest["files"]["../escape"] = {}
            (root / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                prepare_bundle(root, root / "out")


if __name__ == "__main__":
    unittest.main()
