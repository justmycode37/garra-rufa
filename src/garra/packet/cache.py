"""Disk cache for per-anchor API responses and query→anchor registry."""

from __future__ import annotations

import json
import re
from pathlib import Path

from garra.paths import DATA_DIR
from garra.sources.envelope import now_iso

CACHE_ROOT = DATA_DIR / "cache" / "connections"
REGISTRY_PATH = CACHE_ROOT / "query_registry.json"
PACKET_SCHEMA = 1
PACKET_BUILD_VERSION = 2


def normalize_query_key(query: str) -> str:
    q = " ".join(query.strip().lower().split())
    return q


def anchor_dir(anchor_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", anchor_id)
    return CACHE_ROOT / safe


def read_json(path: Path) -> dict | list | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict | list, *, retrieved_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wrapped = {"retrieved_at": retrieved_at, "body": payload}
    path.write_text(json.dumps(wrapped, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_cached(path: Path) -> tuple[str | None, dict | list | None]:
    raw = read_json(path)
    if not isinstance(raw, dict) or "body" not in raw:
        return None, None
    return raw.get("retrieved_at"), raw["body"]


def load_registry() -> dict:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    raw = read_json(REGISTRY_PATH)
    if not isinstance(raw, dict):
        return {"version": 1, "updated_at": None, "queries": {}}
    raw.setdefault("version", 1)
    raw.setdefault("queries", {})
    return raw


def save_registry(registry: dict) -> None:
    registry["updated_at"] = now_iso()
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def register_query(
    query: str,
    *,
    anchor_id: str,
    anchor_name: str = "",
    extra_keys: list[str] | None = None,
) -> None:
    registry = load_registry()
    queries = registry["queries"]
    payload = {
        "anchor_id": anchor_id,
        "anchor_name": anchor_name,
        "registered_at": now_iso(),
    }
    keys = {normalize_query_key(query), normalize_query_key(anchor_id)}
    if extra_keys:
        keys.update(normalize_query_key(k) for k in extra_keys if k)
    for key in keys:
        if key:
            queries[key] = payload
    save_registry(registry)


def lookup_anchor_id(query: str) -> str | None:
    key = normalize_query_key(query)
    reg = load_registry()["queries"].get(key)
    if reg:
        return reg.get("anchor_id")
    if key.upper().startswith("mondo:"):
        return query.strip()
    return None


def load_stored_packet(anchor_id: str) -> dict | None:
    path = anchor_dir(anchor_id) / "packet.json"
    if not path.is_file():
        return None
    packet = read_json(path)
    if not isinstance(packet, dict):
        return None
    return packet


def load_manifest(anchor_id: str) -> dict | None:
    ts, body = read_cached(anchor_dir(anchor_id) / "manifest.json")
    if not isinstance(body, dict):
        return None
    body.setdefault("retrieved_at", ts)
    return body


def packet_is_complete(
    packet: dict,
    *,
    candidate_limit: int,
    semsim_limit: int | None = None,
) -> bool:
    if packet.get("schema_version") != PACKET_SCHEMA:
        return False
    anchor = packet.get("anchor") or {}
    if not anchor.get("id") or not anchor.get("name"):
        return False
    meta = packet.get("fetch_meta") or {}
    if meta.get("build_version") != PACKET_BUILD_VERSION:
        return False
    stored_candidates = meta.get("candidate_limit")
    if stored_candidates is not None and stored_candidates < candidate_limit:
        return False
    stored_semsim = meta.get("semsim_limit")
    if (
        semsim_limit is not None
        and stored_semsim is not None
        and stored_semsim < semsim_limit
    ):
        return False
    # Empty candidates is valid (no semsim matches) but packet must be structurally valid.
    if "candidates" not in packet or "evidence" not in packet:
        return False
    manifest = load_manifest(anchor["id"])
    if manifest is None:
        return False
    return (anchor_dir(anchor["id"]) / "packet.json").is_file()


def try_load_complete_packet(
    query: str,
    *,
    candidate_limit: int,
    semsim_limit: int = 20,
) -> dict | None:
    anchor_id = lookup_anchor_id(query)
    if not anchor_id:
        return None
    packet = load_stored_packet(anchor_id)
    if packet is None:
        return None
    if not packet_is_complete(
        packet, candidate_limit=candidate_limit, semsim_limit=semsim_limit
    ):
        return None
    return packet


def rebuild_registry_from_disk() -> dict:
    """Scan cache dirs and rebuild query_registry from packet fetch_meta."""
    registry = {"version": 1, "updated_at": now_iso(), "queries": {}}
    if not CACHE_ROOT.is_dir():
        save_registry(registry)
        return registry
    count = 0
    for child in sorted(CACHE_ROOT.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        packet_path = child / "packet.json"
        if not packet_path.is_file():
            continue
        packet = read_json(packet_path)
        if not isinstance(packet, dict):
            continue
        anchor = packet.get("anchor") or {}
        anchor_id = anchor.get("id")
        if not anchor_id:
            continue
        meta = packet.get("fetch_meta") or {}
        query = meta.get("query") or anchor.get("name") or anchor_id
        keys = {normalize_query_key(query), normalize_query_key(anchor_id)}
        name = anchor.get("name") or ""
        if name:
            keys.add(normalize_query_key(name))
        payload = {
            "anchor_id": anchor_id,
            "anchor_name": name,
            "registered_at": meta.get("built_at") or now_iso(),
            "cache_dir": str(child),
        }
        for key in keys:
            if key:
                registry["queries"][key] = payload
        count += 1
    registry["anchors_indexed"] = count
    save_registry(registry)
    return registry


def cache_stats() -> dict:
    registry = load_registry()
    anchors = 0
    packets = 0
    if CACHE_ROOT.is_dir():
        for child in CACHE_ROOT.iterdir():
            if child.is_dir() and (child / "packet.json").is_file():
                anchors += 1
                packets += 1
    return {
        "cache_root": str(CACHE_ROOT),
        "registry_queries": len(registry.get("queries") or {}),
        "anchor_dirs_with_packet": anchors,
        "registry_updated_at": registry.get("updated_at"),
    }
