"""Batch prefetch connections packets for a curated anchor list (not all 7200 diseases)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from garra.packet import cache as disk
from garra.packet.build import build_connections_packet
from garra.packet.fetch import unavailable_message
from garra.paths import RAW_DIR
from garra.sources.envelope import now_iso

DEFAULT_ANCHORS_FILE = Path(__file__).resolve().parents[3] / "examples" / "anchors.txt"
PREFETCH_INDEX = disk.CACHE_ROOT / "prefetch_index.jsonl"
JSONL_MAX_DEFAULT = 100
JSONL_HARD_MAX = 7200


@dataclass(frozen=True)
class AnchorJob:
    query: str
    source_row: int | None = None
    gard_curie: str | None = None


def load_queries_from_file(path: Path) -> list[AnchorJob]:
    jobs: list[AnchorJob] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        jobs.append(AnchorJob(query=line))
    if not jobs:
        raise ValueError(f"No anchors found in {path}")
    return jobs


def _count_jsonl_lines(path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                count += 1
    return count


def load_jobs_from_raresource(
    path: Path,
    *,
    limit: int | None,
    offset: int = 0,
    full_catalog: bool = False,
) -> list[AnchorJob]:
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if not path.is_file():
        raise FileNotFoundError(path)

    if full_catalog:
        limit = _count_jsonl_lines(path)
    elif limit is None:
        limit = JSONL_MAX_DEFAULT

    if limit < 1 or limit > JSONL_HARD_MAX:
        raise ValueError(f"limit must be between 1 and {JSONL_HARD_MAX}")

    jobs: list[AnchorJob] = []
    with path.open(encoding="utf-8") as fh:
        for row_idx, line in enumerate(fh):
            if row_idx < offset:
                continue
            if len(jobs) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            name = (record.get("disease_name") or "").strip()
            if name:
                jobs.append(
                    AnchorJob(
                        query=name,
                        source_row=record.get("source_row"),
                        gard_curie=record.get("gard_curie"),
                    )
                )
    if not jobs:
        raise ValueError(f"No disease names loaded from {path} (offset={offset}, limit={limit})")
    return jobs


def dedupe_jobs(jobs: list[AnchorJob]) -> list[AnchorJob]:
    seen: set[str] = set()
    out: list[AnchorJob] = []
    for job in jobs:
        key = disk.normalize_query_key(job.query)
        if key in seen:
            continue
        seen.add(key)
        out.append(job)
    return out


def prefetch_packets(
    jobs: list[AnchorJob],
    *,
    use_cache: bool = True,
    skip_cached: bool = True,
    candidate_limit: int = 5,
    semsim_limit: int = 20,
    sleep_seconds: float = 1.0,
    dry_run: bool = False,
) -> dict:
    """Fetch packets sequentially; writes `data/cache/connections/prefetch_index.jsonl`."""
    started = now_iso()
    results: list[dict] = []
    disk.CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    jobs = dedupe_jobs(jobs)

    for idx, job in enumerate(jobs):
        query = job.query
        entry: dict = {
            "index": idx,
            "query": query,
            "status": "pending",
            "started_at": now_iso(),
        }
        if job.gard_curie:
            entry["gard_curie"] = job.gard_curie
        if job.source_row is not None:
            entry["source_row"] = job.source_row

        if dry_run:
            entry["status"] = "dry_run"
            results.append(entry)
            continue

        try:
            if skip_cached and use_cache:
                packet = disk.try_load_complete_packet(
                    query,
                    candidate_limit=candidate_limit,
                    semsim_limit=semsim_limit,
                )
                if packet is not None:
                    anchor_id = packet["anchor"]["id"]
                    entry.update(
                        {
                            "status": "ok",
                            "anchor_id": anchor_id,
                            "cache_hit": True,
                            "network_skipped": True,
                            "anchor_name": packet["anchor"].get("name"),
                            "candidate_count": len(packet.get("candidates") or []),
                            "cache_dir": (packet.get("fetch_meta") or {}).get("cache_dir")
                            or str(disk.anchor_dir(anchor_id)),
                            "finished_at": now_iso(),
                        }
                    )
                    results.append(entry)
                    _append_index(entry)
                    continue

            packet = build_connections_packet(
                query,
                use_cache=use_cache,
                candidate_limit=candidate_limit,
                semsim_limit=semsim_limit,
            )
            anchor_id = packet["anchor"]["id"]
            extra = [anchor_id, packet["anchor"].get("name") or ""]
            if job.gard_curie:
                extra.append(job.gard_curie)
            disk.register_query(
                query,
                anchor_id=anchor_id,
                anchor_name=packet["anchor"].get("name") or "",
                extra_keys=extra,
            )
            entry.update(
                {
                    "status": "ok",
                    "anchor_id": anchor_id,
                    "cache_hit": False,
                    "anchor_name": packet["anchor"].get("name"),
                    "candidate_count": len(packet.get("candidates") or []),
                    "cache_dir": (packet.get("fetch_meta") or {}).get("cache_dir")
                    or str(disk.anchor_dir(anchor_id)),
                    "finished_at": now_iso(),
                }
            )
        except Exception as exc:
            entry.update(
                {
                    "status": "error",
                    "message": unavailable_message(exc),
                    "error": str(exc),
                    "finished_at": now_iso(),
                }
            )

        results.append(entry)
        _append_index(entry)

        if sleep_seconds > 0 and idx + 1 < len(jobs) and not dry_run:
            if entry.get("status") == "ok" and entry.get("network_skipped"):
                continue
            time.sleep(sleep_seconds)

    ok = sum(1 for r in results if r.get("status") == "ok")
    skipped = sum(1 for r in results if r.get("network_skipped"))
    errors = sum(1 for r in results if r.get("status") == "error")
    report = {
        "status": "ok" if errors == 0 else "partial" if ok else "error",
        "started_at": started,
        "finished_at": now_iso(),
        "total": len(jobs),
        "ok": ok,
        "network_skipped": skipped,
        "errors": errors,
        "dry_run": dry_run,
        "index_path": str(PREFETCH_INDEX),
        "cache_stats": disk.cache_stats(),
        "results": results,
    }
    report_path = disk.CACHE_ROOT / "prefetch_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def _append_index(entry: dict) -> None:
    with PREFETCH_INDEX.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def default_raresource_jsonl() -> Path:
    return RAW_DIR / "raresource" / "diseases.jsonl"
