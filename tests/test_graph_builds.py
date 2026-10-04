import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from garra.ui import graphs
from garra.ui.graphs import GraphBuilds
from garra.ui.server import create_server
from garra.ui.service import InputError

# Stands in for the pipelines: writes what each script would, or fails on "broken".
STUB = r'''
import json, sys
from pathlib import Path
path, args = Path(sys.argv[1]), sys.argv[2:]
script = path.name if path.parent.name == "query-test" else f"{path.parent.name}/{path.name}"
out = Path(args[args.index("-o") + 1]) if "-o" in args else None
if script == "main.py":
    if args[0] == "broken query":
        sys.exit(3)
    out.write_text(json.dumps({"query": args[0]}))
elif script == "web_export.py":
    run = Path(args[0]); out.mkdir(exist_ok=True)
    entry = {"id": "run", "label": json.loads(run.read_text())["query"].title(),
             "present": "run.present.json", "evidence": None}
    (out / "run.present.json").write_text(json.dumps({"focus": {}}))
    if run.with_name("run.kg.json").exists():
        entry["evidence"] = "run.evidence.json"
        (out / "run.evidence.json").write_text(json.dumps({"nodes": []}))
    (out / "index.json").write_text(json.dumps([entry]))
else:  # literature/main.py and evidence/main.py
    out.write_text("{}")
'''


class GraphBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        stub = self.tmp / "stub.py"
        stub.write_text(STUB)
        self.builds = GraphBuilds(self.tmp / "builds", python=sys.executable)
        # run the stub with the script path as its first argument
        self.builds.python = sys.executable
        self._orig = graphs.subprocess.run

        def run(cmd, **kw):
            return self._orig([cmd[0], str(stub), *cmd[1:]], **kw)
        graphs.subprocess.run = run
        self.addCleanup(lambda: setattr(graphs.subprocess, "run", self._orig))
        self._key = graphs._has_llm_key
        graphs._has_llm_key = lambda: True
        self.addCleanup(lambda: setattr(graphs, "_has_llm_key", self._key))

    def wait(self, bid):
        for _ in range(200):
            b = self.builds.get(bid)
            if b["state"] in ("done", "failed"):
                return b
            time.sleep(0.05)
        self.fail("build did not finish")

    def test_overview_build_publishes_present_view(self):
        b = self.builds.start({"query": "Marfan  syndrome"})
        self.assertEqual(b["query"], "Marfan syndrome")
        self.assertEqual(b["stages"], ["graph", "overview"])
        done = self.wait(b["id"])
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["label"], "Marfan Syndrome")
        self.assertTrue(self.builds.view(b["id"], "present"))
        self.assertIsNone(self.builds.view(b["id"], "evidence"))
        # the same query reuses the finished build
        self.assertEqual(self.builds.start({"query": "marfan syndrome"})["id"], b["id"])

    def test_disease_id_build_passes_its_label(self):
        calls = []
        stubbed = graphs.subprocess.run
        graphs.subprocess.run = lambda cmd, **kw: (calls.append(cmd), stubbed(cmd, **kw))[1]
        b = self.builds.start({"query": "OMIM:154700", "label": "Marfan syndrome"})
        self.assertEqual(b["label"], "Marfan syndrome")
        self.assertEqual(self.wait(b["id"])["state"], "done")
        self.assertEqual(calls[0][-2:], ["--label", "Marfan syndrome"])
        with self.assertRaises(InputError):
            self.builds.start({"query": "OMIM:154700", "label": "see https://example.org"})

    def test_symptom_build_keeps_its_label_but_no_label_flag(self):
        calls = []
        stubbed = graphs.subprocess.run
        graphs.subprocess.run = lambda cmd, **kw: (calls.append(cmd), stubbed(cmd, **kw))[1]
        b = self.builds.start({"query": "HP:0001627, chest pain", "label": "Heart: chest pain"})
        self.assertEqual(b["label"], "Heart: chest pain")
        self.assertEqual(self.wait(b["id"])["state"], "done")
        self.assertNotIn("--label", calls[0])

    def test_evidence_build_runs_all_stages(self):
        b = self.builds.start({"query": "PMM2", "evidence": True})
        self.assertEqual(b["stages"], ["graph", "overview", "papers", "evidence", "export"])
        done = self.wait(b["id"])
        self.assertEqual(done["done"], b["stages"])
        self.assertTrue(self.builds.view(b["id"], "evidence"))
        self.assertTrue(done["views"]["present"])

    def test_same_query_is_served_from_the_cache(self):
        b = self.wait(self.builds.start({"query": "Pompe disease"})["id"])
        again = self.builds.start({"query": "pompe  DISEASE"})
        self.assertEqual((again["id"], again["state"]), (b["id"], "done"))

    def test_evidence_is_added_to_a_cached_overview(self):
        b = self.wait(self.builds.start({"query": "Marfan"})["id"])
        self.assertFalse(b["views"].get("evidence"))
        more = self.builds.add_evidence(b["id"])
        self.assertEqual((more["id"], more["state"], more["evidence"]), (b["id"], "queued", True))
        done = self.wait(b["id"])
        self.assertEqual(done["done"], ["graph", "overview", "papers", "evidence", "export"])
        self.assertTrue(self.builds.view(b["id"], "evidence"))
        log = (self.builds.root / b["id"] / "build.log").read_text(encoding="utf-8")
        self.assertEqual(log.count("== graph =="), 1)  # the overview is not rebuilt
        # asking again (or building the query with evidence) reuses the finished build
        self.assertEqual(self.builds.add_evidence(b["id"])["state"], "done")
        self.assertEqual(self.builds.start({"query": "Marfan", "evidence": True})["id"], b["id"])

    def test_evidence_query_upgrades_the_cached_overview(self):
        b = self.wait(self.builds.start({"query": "Fabry"})["id"])
        more = self.builds.start({"query": "Fabry", "evidence": True})
        self.assertEqual((more["id"], more["state"]), (b["id"], "queued"))
        self.assertTrue(self.wait(b["id"])["views"]["evidence"])

    def test_evidence_needs_a_finished_overview(self):
        self.assertIsNone(self.builds.add_evidence("nope-0123abcd"))
        self.assertIsNone(self.builds.add_evidence("../x"))
        failed = self.wait(self.builds.start({"query": "broken query"})["id"])
        with self.assertRaises(InputError):
            self.builds.add_evidence(failed["id"])
        graphs._has_llm_key = lambda: False
        b = self.wait(self.builds.start({"query": "Pompe"})["id"])
        with self.assertRaises(InputError):
            self.builds.add_evidence(b["id"])

    def test_failed_stage_is_reported_and_can_be_retried(self):
        b = self.wait(self.builds.start({"query": "broken query"})["id"])
        self.assertEqual(b["state"], "failed")
        self.assertIn("graph stage failed", b["error"])
        self.assertTrue(any("== graph ==" in line for line in b["log"]))
        self.assertEqual(self.builds.start({"query": "broken query"})["state"], "queued")

    def test_restart_marks_running_builds_failed(self):
        bid = "x-0123abcd"
        (self.builds.root / bid).mkdir()
        (self.builds.root / bid / "status.json").write_text(json.dumps({
            "id": bid, "query": "x", "label": "x", "evidence": False, "state": "running",
            "stage": "graph", "stages": ["graph"], "done": [], "views": {}, "error": None,
            "created": 1, "updated": 1}))
        again = GraphBuilds(self.builds.root)
        self.assertEqual(again.get(bid)["state"], "failed")

    def test_rejects_bad_input(self):
        for body in [{"query": "x"}, {"query": "see https://example.org"}, {"query": "ok", "x": 1},
                     ["Marfan"], {"query": "y" * 121}, {"query": "rm -rf; <b>"}, {"query": "-n 99 Marfan"}]:
            with self.subTest(body=body), self.assertRaises(InputError):
                self.builds.start(body)
        graphs._has_llm_key = lambda: False
        with self.assertRaises(InputError):
            self.builds.start({"query": "Pompe disease", "evidence": True})
        for bid, kind in [("../etc", "present"), ("x-0123abcd", "../../status"), ("nope", "present")]:
            self.assertIsNone(self.builds.view(bid, kind))

    def test_http_routes(self):
        server = create_server(port=0, graphs=self.builds)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        req = Request(f"{base}/api/graphs/build", data=json.dumps({"query": "Fabry disease"}).encode(),
                      headers={"Content-Type": "application/json"})
        bid = json.load(urlopen(req))["id"]
        self.wait(bid)
        listing = json.load(urlopen(f"{base}/api/graphs"))
        self.assertTrue(listing["enabled"])
        self.assertEqual([b["id"] for b in listing["builds"]], [bid])
        self.assertEqual(json.load(urlopen(f"{base}/api/graphs/{bid}"))["state"], "done")
        self.assertEqual(json.load(urlopen(f"{base}/api/graphs/{bid}/present")), {"focus": {}})
        with self.assertRaises(HTTPError) as err:
            urlopen(f"{base}/api/graphs/{bid}/evidence")
        self.assertEqual(err.exception.code, 404)
        more = Request(f"{base}/api/graphs/{bid}/evidence", data=b"{}", headers={"Content-Type": "application/json"})
        self.assertEqual(json.load(urlopen(more))["evidence"], True)
        self.wait(bid)
        self.assertEqual(json.load(urlopen(f"{base}/api/graphs/{bid}/evidence")), {"nodes": []})
        missing = Request(f"{base}/api/graphs/nope-0123abcd/evidence", data=b"{}", headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as err:
            urlopen(missing)
        self.assertEqual(err.exception.code, 404)
        bad = Request(f"{base}/api/graphs/build", data=b'{"query": "x"}', headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as err:
            urlopen(bad)
        self.assertEqual(err.exception.code, 400)

    def test_server_without_builds(self):
        server = create_server(port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        with self.assertRaises(HTTPError) as err:
            urlopen(f"http://127.0.0.1:{server.server_port}/api/graphs")
        self.assertEqual(err.exception.code, 503)


if __name__ == "__main__":
    unittest.main()
