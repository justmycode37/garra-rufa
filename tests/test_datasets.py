import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from garra import datasets
from garra.ui import server


def bundle(directory, files):
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "files": {}}
    for name, raw in files.items():
        (directory / (name + ".gz")).write_bytes(gzip.compress(raw))
        manifest["files"][name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    (directory / "manifest.json").write_text(json.dumps(manifest))


class EnsureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patches = [mock.patch.object(datasets, "BUNDLE", self.tmp / "bundle"),
                   mock.patch.object(datasets, "_from_release", return_value=False),
                   mock.patch.dict(datasets._tried, clear=True)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_existing_file_is_returned_untouched(self):
        path = self.tmp / "bridge.json"
        path.write_text("{}")
        self.assertEqual(datasets.ensure(path), path)
        self.assertIsNone(datasets.ensure(None))

    def test_missing_file_is_restored_from_the_bundle(self):
        bundle(self.tmp / "bundle", {"bridge.json": b'{"schema_version": 1}'})
        path = self.tmp / "data" / "bridge.json"
        self.assertEqual(datasets.ensure(path), path)
        self.assertEqual(path.read_bytes(), b'{"schema_version": 1}')

    def test_checksum_mismatch_and_unknown_files_are_skipped(self):
        bundle(self.tmp / "bundle", {"bridge.json": b"{}"})
        manifest = json.loads((self.tmp / "bundle/manifest.json").read_text())
        manifest["files"]["bridge.json"]["sha256"] = "0" * 64
        (self.tmp / "bundle/manifest.json").write_text(json.dumps(manifest))
        self.assertIsNone(datasets.ensure(self.tmp / "bridge.json"))
        self.assertIsNone(datasets.ensure(self.tmp / "unknown.json"))
        self.assertFalse((self.tmp / "bridge.json").exists())


class ServerStartupTests(unittest.TestCase):
    def test_server_starts_when_every_dataset_is_missing(self):
        tmp = Path(tempfile.mkdtemp())
        captured = {}

        def fake_server(**options):
            captured.update(options)
            return mock.Mock(server_port=0, serve_forever=mock.Mock(side_effect=KeyboardInterrupt))

        with mock.patch.object(datasets, "BUNDLE", tmp / "none"), \
                mock.patch.object(datasets, "_from_release", return_value=False), \
                mock.patch.dict(datasets._tried, clear=True), \
                mock.patch.object(server, "create_server", fake_server):
            code = server.main(["--bridge", str(tmp / "bridge.json"), "--ontology", str(tmp / "hp.obo"),
                                "--atlas", str(tmp / "atlas.sqlite"), "--no-graph-builds"])
        self.assertEqual(code, 0)
        self.assertNotIn("regions", captured)
        self.assertEqual(captured["discovery"].metadata()["coverage"]["clusters"], 0)


if __name__ == "__main__":
    unittest.main()
