"""List the registry and download the default bulk files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import BULK_SOURCES, MANUAL_SOURCES, QUERY_SOURCES, validate_catalog
from .client import fetch_bulk, fetch_defaults


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch approved rare-disease sources.")
    parser.add_argument("--list", action="store_true", help="print the registry and exit")
    parser.add_argument("--dest", default="data/raw", help="directory for downloaded files")
    parser.add_argument("--only", action="append", default=[], help="bulk source id; repeatable")
    parser.add_argument(
        "--allow-large",
        action="store_true",
        help="permit files over 100 MB, including ClinVar variant_summary",
    )
    args = parser.parse_args(argv)
    validate_catalog()

    if args.list:
        _print_registry()
        return 0

    dest = Path(args.dest)
    unknown = [name for name in args.only if name not in BULK_SOURCES]
    if unknown:
        print(f"unknown bulk source: {', '.join(unknown)}", file=sys.stderr)
        print("run with --list to see ids", file=sys.stderr)
        return 2
    if args.only:
        records = [
            fetch_bulk(BULK_SOURCES[name], dest, allow_large=args.allow_large) for name in args.only
        ]
    else:
        records = fetch_defaults(dest, allow_large=args.allow_large)

    failed = [row for row in records if row["status"] != "ok"]
    print(json.dumps({"saved": len(records) - len(failed), "failed": len(failed), "sources": records}, indent=2))
    return 1 if failed else 0


def _print_registry() -> None:
    groups = (("bulk", BULK_SOURCES), ("query", QUERY_SOURCES), ("manual", MANUAL_SOURCES))
    for label, table in groups:
        print(f"\n[{label}]")
        for source in table.values():
            flag = "default" if source.default else "optional"
            if source.access != "bulk":
                flag = source.access
            print(f"  {source.id:36} {flag:10} {source.publisher}")
            print(f"    {source.role}")
            if source.note:
                print(f"    {source.note}")


if __name__ == "__main__":
    raise SystemExit(main())
