"""Replay the requests in a GARRA_LOCAL_PROFILE log against the local handlers and report
latency per handler and the slowest calls.

  GARRA_LOCAL_PROFILE=data/bench/run.tsv python src/query-test/main.py "Marfan syndrome"
  PYTHONPATH=src python -m garra.local bench data/bench/run.tsv [--repeat 3] [--top 15]

The first pass runs against a cold-ish page cache (whatever the OS still holds), later
passes show warm latency.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from garra.local import router


def load(path: Path) -> list[tuple[str, str, dict, bytes | None]]:
    calls, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        p = line.split("\t")
        if len(p) < 7:
            continue
        key = (p[3], p[4], p[5], p[6])
        if key in seen:
            continue
        seen.add(key)
        body = json.loads(p[6]) or None
        calls.append((p[3], p[4], json.loads(p[5]), body.encode() if body else None))
    return calls


def run(path: Path, repeat: int = 2, top: int = 15) -> int:
    calls = load(path)
    print(f"{len(calls)} distinct requests from {path}")
    for rnd in range(repeat):
        per: dict[str, list[float]] = defaultdict(list)
        slow = []
        t_all = time.perf_counter()
        for method, url, params, body in calls:
            t = time.perf_counter()
            reply = router.route(method, url, params=params, data=body)
            dt = time.perf_counter() - t
            name = url.split("//", 1)[-1].split("/", 1)[0]
            per[name].append(dt)
            slow.append((dt, reply is not None, url,
                         json.dumps(params)[:150] if params else (body or b"")[:150]))
        total = time.perf_counter() - t_all
        print(f"\npass {rnd + 1}: {total:.2f} s")
        for name, ts in sorted(per.items(), key=lambda kv: -sum(kv[1])):
            ts.sort()
            print(f"  {name:36} n={len(ts):4}  total={sum(ts):7.3f}s  "
                  f"median={ts[len(ts) // 2] * 1000:7.1f}ms  max={ts[-1] * 1000:7.1f}ms")
        if rnd == repeat - 1:
            print("\nslowest:")
            for dt, ok, url, what in sorted(slow, key=lambda x: -x[0])[:top]:
                print(f"  {dt * 1000:8.1f}ms {'local' if ok else 'miss '} {url}  {what}")
    return 0
