"""Resolve a user query to a Monarch anchor using disk cache when possible."""

from __future__ import annotations

from garra.packet import cache as disk
from garra.packet.fetch import monarch_search_disease


def resolve_anchor_query(query: str, *, use_cache: bool = True) -> tuple[dict, bool]:
    """Return (search_payload, network_search_used).

    search_payload matches monarch_search_disease output shape.
    """
    stripped = query.strip()
    if use_cache:
        anchor_id = disk.lookup_anchor_id(stripped)
        if anchor_id:
            path = disk.anchor_dir(anchor_id) / "monarch_search.json"
            ts, body = disk.read_cached(path)
            if isinstance(body, dict) and body.get("item"):
                return body, False

    if stripped.upper().startswith("MONDO:"):
        anchor_id = stripped
        if use_cache:
            path = disk.anchor_dir(anchor_id) / "monarch_search.json"
            ts, body = disk.read_cached(path)
            if isinstance(body, dict) and body.get("item"):
                return body, False

    search = monarch_search_disease(stripped)
    item = search["item"]
    disk.register_query(
        stripped,
        anchor_id=item["id"],
        anchor_name=item.get("name") or "",
        extra_keys=[item.get("name") or ""],
    )
    return search, True
