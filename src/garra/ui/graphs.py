"""Graph builds for the webapp's graph pages: a query runs the query-test pipelines in the
background and the result is exported for the viewers (src/query-test/web_export.py).

  overview   main.py <query> -o run.json, then web_export -> present.json  (minutes)
  evidence   literature/main.py run.json, evidence/main.py (LLM, OPENROUTER_API_KEY),
             then web_export again -> evidence.json  (much longer; optional)

One build runs at a time, the others queue. Every build lives in <root>/<id>/ with its
status.json, the pipeline outputs and a log; the overview is readable as soon as its
stage is done, while the evidence stage still runs. Builds interrupted by a restart are
marked as failed when the service starts.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from .service import InputError

REPO = Path(__file__).resolve().parents[3]
QUERY_TEST = REPO / "src" / "query-test"
DEFAULT_ROOT = REPO / "data" / "web-graphs"
QUERY = re.compile(r"^\w[\w\s,.;:'()+/-]{1,119}$", re.UNICODE)  # argv: never a leading '-'
ID = re.compile(r"^[a-z0-9-]{1,60}-[0-9a-f]{8}$")
CURIE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*:[A-Za-z0-9_.-]+$")
MAX_QUEUED = 4
STAGE_TIMEOUT = {"graph": 20 * 60, "overview": 5 * 60, "papers": 30 * 60,
                 "evidence": 3 * 60 * 60, "export": 10 * 60}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "graph"


def _has_llm_key() -> bool:
    if os.environ.get("OPENROUTER_API_KEY"):
        return True
    env = REPO / ".env"
    return env.exists() and any(
        line.strip().removeprefix("export ").startswith("OPENROUTER_API_KEY=")
        and line.split("=", 1)[1].strip().strip("'\"")
        for line in env.read_text(encoding="utf-8").splitlines())


class GraphBuilds:
    def __init__(self, root: Path | None = None, python: str | None = None):
        self.root = Path(root or os.environ.get("GARRA_WEB_GRAPHS_DIR") or DEFAULT_ROOT)
        self.python = python or os.environ.get("GARRA_PYTHON") or sys.executable
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.queue: deque[str] = deque()
        self.wake = threading.Event()
        self.builds: dict[str, dict] = {}
        for status in self.root.glob("*/status.json"):
            try:
                b = json.loads(status.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if b.get("state") in ("queued", "running"):
                b.update(state="failed", error="The build was interrupted by a restart.")
                self._save(b)
            self.builds[b["id"]] = b
        threading.Thread(target=self._worker, daemon=True, name="graph-builds").start()

    # -- public API ------------------------------------------------------------------
    def capabilities(self) -> dict:
        return {"enabled": True, "evidence": _has_llm_key()}

    def list(self) -> dict:
        with self.lock:
            items = sorted(self.builds.values(), key=lambda b: -b["created"])
            return {**self.capabilities(), "builds": [self._public(b) for b in items[:50]]}

    def get(self, bid: str) -> dict | None:
        with self.lock:
            b = self.builds.get(bid) if ID.match(bid or "") else None
            return self._public(b) if b else None

    def view(self, bid: str, kind: str) -> Path | None:
        if not ID.match(bid or "") or kind not in ("present", "evidence"):
            return None
        with self.lock:
            b = self.builds.get(bid)
        path = self.root / bid / f"{kind}.json"
        return path if b and b["views"].get(kind) and path.exists() else None

    def start(self, body) -> dict:
        if not isinstance(body, dict) or set(body) - {"query", "evidence", "label"}:
            raise InputError("Send a query and, optionally, evidence: true")
        query = " ".join(str(body.get("query") or "").split())
        if not QUERY.match(query) or "://" in query:
            raise InputError("Use a disease, symptom or gene name (2-120 characters)")
        # a disease picked by id (atlas) keeps its name as label: main.py --label
        label = " ".join(str(body.get("label") or "").split())[:200]
        if label and (not CURIE.match(query) or any(ord(c) < 32 for c in label) or "://" in label):
            raise InputError("A label is only accepted with a disease id such as ORPHA:558")
        evidence = body.get("evidence") is True
        if evidence and not _has_llm_key():
            raise InputError("The evidence graph needs OPENROUTER_API_KEY on the research service")
        key = hashlib.sha1(f"{query.lower()}|{evidence}".encode()).hexdigest()[:8]
        bid = f"{_slug(query)}-{key}"
        with self.lock:
            old = self.builds.get(bid)
            if old and old["state"] != "failed":
                return self._public(old)  # same query: reuse the running / finished build
            if sum(b["state"] == "queued" for b in self.builds.values()) >= MAX_QUEUED:
                raise InputError("Several graphs are already being built. Try again later")
            stages = ["graph", "overview"] + (["papers", "evidence", "export"] if evidence else [])
            b = {"id": bid, "query": query, "label": label or query, "input_label": label,
                 "evidence": evidence,
                 "state": "queued", "stage": None, "stages": stages, "done": [],
                 "views": {}, "error": None, "created": time.time(), "updated": time.time()}
            self.builds[bid] = b
            (self.root / bid).mkdir(exist_ok=True)
            self._save(b)
            self.queue.append(bid)
        self.wake.set()
        return self._public(b)

    # -- worker ----------------------------------------------------------------------
    def _worker(self):
        while True:
            self.wake.wait()
            with self.lock:
                bid = self.queue.popleft() if self.queue else None
                if not self.queue:
                    self.wake.clear()
            if bid:
                self._run(bid)

    def _run(self, bid: str):
        d = self.root / bid
        b = self.builds[bid]
        run, papers, kg = d / "run.json", d / "run.papers.json", d / "run.kg.json"
        out = ["-o", str(d / "export")]
        commands = {
            "graph": [str(QUERY_TEST / "main.py"), b["query"], "-o", str(run),
                      *(["--label", b["input_label"]] if b.get("input_label") else [])],
            "overview": [str(QUERY_TEST / "web_export.py"), str(run), *out],
            "papers": [str(QUERY_TEST / "literature" / "main.py"), str(run), "-o", str(papers)],
            "evidence": [str(QUERY_TEST / "evidence" / "main.py"), str(papers), "-o", str(kg),
                         "--max-fulltext", "15", "--transfer-nodes", "3"],
            "export": [str(QUERY_TEST / "web_export.py"), str(run), *out],
        }
        self._update(b, state="running")
        for stage in b["stages"]:
            self._update(b, stage=stage)
            try:
                with open(d / "build.log", "a", encoding="utf-8") as log:
                    log.write(f"\n== {stage} ==\n")
                    log.flush()
                    proc = subprocess.run([self.python, *commands[stage]], cwd=REPO,
                                          stdout=log, stderr=subprocess.STDOUT,
                                          timeout=STAGE_TIMEOUT[stage],
                                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                if proc.returncode:
                    raise RuntimeError(f"{stage} stage failed (exit {proc.returncode})")
                if stage in ("overview", "export"):
                    self._publish(b, d / "export")
            except subprocess.TimeoutExpired:
                return self._update(b, state="failed", error=f"The {stage} stage timed out.")
            except Exception as exc:  # noqa: BLE001 - reported to the user, logged in build.log
                return self._update(b, state="failed", error=str(exc))
            self._update(b, done=[*b["done"], stage])
        self._update(b, state="done", stage=None)

    def _publish(self, b: dict, export: Path):
        """Copy the exported views to <id>/present.json and <id>/evidence.json."""
        index = export / "index.json"
        if not index.exists():
            return
        entry = next(iter(json.loads(index.read_text(encoding="utf-8"))), None)
        index.unlink()  # the next export writes a fresh one
        if not entry:
            return
        views = dict(b["views"])
        for kind in ("present", "evidence"):
            if entry.get(kind):
                (export / entry[kind]).replace(self.root / b["id"] / f"{kind}.json")
                views[kind] = True
                if entry.get(f"{kind}Stats"):
                    views[f"{kind}Stats"] = entry[f"{kind}Stats"]
        self._update(b, views=views, label=entry.get("label") or b["label"])

    # -- state -----------------------------------------------------------------------
    def _update(self, b: dict, **changes):
        with self.lock:
            b.update(changes, updated=time.time())
            self._save(b)

    def _save(self, b: dict):
        path = self.root / b["id"] / "status.json"
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def _public(self, b: dict) -> dict:
        log = self.root / b["id"] / "build.log"
        tail = []
        if b["state"] in ("running", "failed") and log.exists():
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = [x[:200] for x in lines[-6:] if x.strip()]
        out = {k: b[k] for k in ("id", "query", "label", "evidence", "state", "stage", "stages",
                                 "done", "error", "created", "updated")}
        return {**out, "views": {k: v for k, v in b["views"].items()}, "log": tail}
