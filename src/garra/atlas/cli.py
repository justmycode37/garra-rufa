"""Command-line entry for backend work (teammate fetch + your graph layer)."""

from __future__ import annotations

import argparse
import json
import sys

from garra.actions.journey import build_journey
from garra.explain.explain import explain_query
from garra.graph.resolve import resolve_query
from garra.ingest.build import build_atlas, missing_sources
from garra.similarity import similar_diseases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rare disease atlas backend")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("missing", help="List default raw files the teammate has not fetched yet")

    build = sub.add_parser("build", help="Build data/atlas.sqlite from data/raw")
    build.add_argument("--json", action="store_true", help="Print build report as JSON")

    resolve = sub.add_parser("resolve", help="Resolve a disease name, id, or gene symbol")
    resolve.add_argument("query")

    similar = sub.add_parser("similar", help="Find similar diseases for a disease id or gene symbol")
    similar.add_argument("target", help="disease_key (MONDO:…) or gene symbol (GAA)")
    similar.add_argument("--limit", type=int, default=5)

    journey = sub.add_parser("journey", help="Full backend path for demo (no UI)")
    journey.add_argument("query")
    journey.add_argument("--offline", action="store_true", help="Skip live trial/grant/paper queries")

    explain = sub.add_parser("explain", help="Journey + family-readable explanation (OpenAI or fallback)")
    explain.add_argument("query")
    explain.add_argument("--offline", action="store_true", help="Skip live trial/grant/paper queries")
    explain.add_argument("--no-openai", action="store_true", help="Use deterministic fallback only")

    args = parser.parse_args(argv)

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

            payload = similar_diseases(gene_key=GENE_SYMBOL_KEY.format(symbol=target.upper()), limit=args.limit)
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
