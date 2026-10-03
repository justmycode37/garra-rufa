"""Command-line entry for backend work (teammate fetch + your graph layer)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from garra.actions.journey import build_journey
from garra.connections import build_connections, render_connections
from garra.explain.explain import explain_query
from garra.graph.resolve import resolve_query
from garra.ingest.build import build_atlas, missing_sources
from garra.packet import cache as packet_cache
from garra.packet.build import build_connections_packet, run_pipeline
from garra.packet.prefetch import (
    DEFAULT_ANCHORS_FILE,
    JSONL_HARD_MAX,
    JSONL_MAX_DEFAULT,
    load_jobs_from_raresource,
    load_queries_from_file,
    prefetch_packets,
)
from garra.similarity import similar_diseases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rare disease atlas backend")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("missing", help="List default raw files the teammate has not fetched yet")

    build = sub.add_parser("build", help="Build data/atlas.sqlite from data/raw")
    build.add_argument("--json", action="store_true", help="Print build report as JSON")

    resolve = sub.add_parser("resolve", help="Resolve a disease name, id, or gene symbol")
    resolve.add_argument("query")

    similar = sub.add_parser(
        "similar", help="Find similar diseases for a disease id or gene symbol"
    )
    similar.add_argument("target", help="disease_key (MONDO:…) or gene symbol (GAA)")
    similar.add_argument("--limit", type=int, default=5)

    journey = sub.add_parser("journey", help="Full backend path for demo (no UI)")
    journey.add_argument("query")
    journey.add_argument(
        "--offline", action="store_true", help="Skip live trial/grant/paper queries"
    )

    explain = sub.add_parser(
        "explain", help="Journey + family-readable explanation (OpenAI or fallback)"
    )
    explain.add_argument("query")
    explain.add_argument(
        "--offline", action="store_true", help="Skip live trial/grant/paper queries"
    )
    explain.add_argument("--no-openai", action="store_true", help="Use deterministic fallback only")

    connections = sub.add_parser(
        "connections", help="Build evidence-linked cards from a fetcher JSON packet (no network)"
    )
    connections.add_argument("packet", type=Path)
    connections.add_argument(
        "--min-score",
        type=float,
        default=50,
        help="0–100 Jaccard display threshold; native metrics are not filtered",
    )
    connections.add_argument("--limit", type=int, default=5)
    connections.add_argument("--html", type=Path, help="Write a standalone browser report")

    packet_cmd = sub.add_parser(
        "packet",
        help="Fetch Monarch/Orphadata/Open Targets for one anchor and write connections packet v1",
    )
    packet_cmd.add_argument("query")
    packet_cmd.add_argument("--no-cache", action="store_true", help="Refetch all API steps")
    packet_cmd.add_argument("--candidate-limit", type=int, default=5)
    packet_cmd.add_argument(
        "--out",
        type=Path,
        help="Copy packet JSON here (also stored under data/cache/connections/)",
    )

    pipeline = sub.add_parser(
        "pipeline",
        help="packet → build_connections → explain_query (single anchor, no bulk disease loop)",
    )
    pipeline.add_argument("query")
    pipeline.add_argument("--no-cache", action="store_true")
    pipeline.add_argument("--candidate-limit", type=int, default=5)
    pipeline.add_argument("--min-score", type=float, default=50)
    pipeline.add_argument("--offline", action="store_true", help="Offline explain/journey actions")
    pipeline.add_argument("--no-openai", action="store_true")
    pipeline.add_argument("--html", type=Path, help="Write connections HTML report")
    pipeline.add_argument(
        "--packet-only",
        action="store_true",
        help="Print only the connections packet JSON",
    )
    pipeline.add_argument(
        "--out",
        type=Path,
        help="Write full pipeline JSON (large; includes packet) to this path",
    )

    prefetch = sub.add_parser(
        "prefetch-packets",
        help="Batch-fetch connection packets for a curated anchor list (not all 7200 diseases)",
    )
    src = prefetch.add_mutually_exclusive_group()
    src.add_argument(
        "--file",
        type=Path,
        help=f"Text file: one disease query per line (default: {DEFAULT_ANCHORS_FILE.name})",
    )
    src.add_argument(
        "--raresource-jsonl",
        type=Path,
        help="RARe-SOURCE diseases.jsonl (requires --limit; capped for safety)",
    )
    prefetch.add_argument(
        "--limit",
        type=int,
        help=f"Max rows from JSONL (default {JSONL_MAX_DEFAULT} when using --raresource-jsonl)",
    )
    prefetch.add_argument("--offset", type=int, default=0, help="JSONL row offset")
    prefetch.add_argument("--no-cache", action="store_true", help="Refetch API steps per anchor")
    prefetch.add_argument(
        "--refetch",
        action="store_true",
        help="Rebuild even when packet.json already exists in cache",
    )
    prefetch.add_argument("--candidate-limit", type=int, default=5)
    prefetch.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Seconds between anchors (rate limit courtesy)",
    )
    prefetch.add_argument("--dry-run", action="store_true", help="List anchors only")
    prefetch.add_argument(
        "--reset-index",
        action="store_true",
        help="Truncate data/cache/connections/prefetch_index.jsonl before this run",
    )
    prefetch.add_argument(
        "--rebuild-registry",
        action="store_true",
        help="Rescan data/cache/connections/*/packet.json into query_registry.json",
    )
    prefetch.add_argument(
        "--full-catalog",
        action="store_true",
        help=f"With --raresource-jsonl: prefetch all rows (max {JSONL_HARD_MAX})",
    )

    sub.add_parser(
        "cache-status",
        help="Show connections packet disk cache and query registry stats",
    )
    sub.add_parser(
        "cache-rebuild-registry",
        help="Rebuild query_registry.json from cached packet.json files",
    )

    args = parser.parse_args(argv)

    if args.cmd == "cache-status":
        print(json.dumps(packet_cache.cache_stats(), indent=2))
        return 0

    if args.cmd == "cache-rebuild-registry":
        report = packet_cache.rebuild_registry_from_disk()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "prefetch-packets":
        try:
            if args.rebuild_registry:
                packet_cache.rebuild_registry_from_disk()
            if args.raresource_jsonl:
                jsonl_path = args.raresource_jsonl
                jobs = load_jobs_from_raresource(
                    jsonl_path,
                    limit=args.limit,
                    offset=args.offset,
                    full_catalog=args.full_catalog,
                )
            else:
                path = args.file or DEFAULT_ANCHORS_FILE
                jobs = load_queries_from_file(path)
                if args.limit is not None:
                    jobs = jobs[: args.limit]
            if args.reset_index:
                idx = packet_cache.CACHE_ROOT / "prefetch_index.jsonl"
                idx.parent.mkdir(parents=True, exist_ok=True)
                idx.write_text("", encoding="utf-8")
            report = prefetch_packets(
                jobs,
                use_cache=not args.no_cache,
                skip_cached=not args.refetch,
                candidate_limit=args.candidate_limit,
                sleep_seconds=args.sleep,
                dry_run=args.dry_run,
            )
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report.get("status") in ("ok", "partial") else 1
        except (OSError, ValueError, FileNotFoundError) as exc:
            print(f"Prefetch failed: {exc}", file=sys.stderr)
            return 2

    if args.cmd == "packet":
        try:
            payload = build_connections_packet(
                args.query,
                use_cache=not args.no_cache,
                candidate_limit=args.candidate_limit,
            )
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        except (OSError, ValueError, LookupError) as exc:
            print(f"Packet fetch failed: {exc}", file=sys.stderr)
            return 2

    if args.cmd == "pipeline":
        result = run_pipeline(
            args.query,
            use_cache=not args.no_cache,
            candidate_limit=args.candidate_limit,
            min_score=args.min_score,
            live=not args.offline,
            use_openai=not args.no_openai,
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        if args.packet_only:
            print(json.dumps(result.get("packet") or result, indent=2, ensure_ascii=False))
        else:
            if args.html and result.get("connections"):
                args.html.write_text(
                    render_connections(result["connections"]), encoding="utf-8"
                )
            slim = {k: v for k, v in result.items() if k != "packet"}
            slim["packet_summary"] = {
                "anchor": (result.get("packet") or {}).get("anchor"),
                "candidate_count": len((result.get("packet") or {}).get("candidates") or []),
                "cache_dir": result.get("cache_dir"),
            }
            print(json.dumps(slim, indent=2, ensure_ascii=False))
        return 0 if result.get("status") == "ok" else 1

    if args.cmd == "connections":
        try:
            packet = json.loads(args.packet.read_text(encoding="utf-8"))
            result = build_connections(packet, min_score=args.min_score, limit=args.limit)
            if args.html:
                args.html.write_text(render_connections(result), encoding="utf-8")
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            print(f"Invalid connection packet: {exc}", file=sys.stderr)
            return 2

    if args.cmd == "missing":
        print(json.dumps(missing_sources(), indent=2))
        return 0

    if args.cmd == "build":
        report = build_atlas()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Built {report['db_path']}")
            print("Counts:", report["counts"])
            missing = report["missing_default_sources"]
            if missing:
                print(f"Still missing {len(missing)} default raw source(s); re-run after fetch.")
        return 0

    if args.cmd == "resolve":
        print(json.dumps(resolve_query(args.query), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "similar":
        target = args.target.strip()
        if ":" in target:
            payload = similar_diseases(disease_key=target, limit=args.limit)
        else:
            from garra.ingest.loaders import GENE_SYMBOL_KEY

            payload = similar_diseases(
                gene_key=GENE_SYMBOL_KEY.format(symbol=target.upper()), limit=args.limit
            )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "journey":
        payload = build_journey(args.query, live=not args.offline)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload.get("status") == "ok" else 1

    if args.cmd == "explain":
        payload = explain_query(
            args.query,
            live=not args.offline,
            use_openai=not args.no_openai,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload.get("status") == "ok" else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
