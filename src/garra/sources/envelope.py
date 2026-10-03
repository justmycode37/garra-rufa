"""Shared result shape for every source read.

Callers get the same fields whether the upstream file downloaded, the query
returned nothing, or the host was refused. A missing route is a status, not
an exception the graph layer has to guess about.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(
    *,
    status: str,
    source_id: str,
    publisher: str,
    license: str,
    url: str,
    message: str = "",
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": status,
        "source_id": source_id,
        "publisher": publisher,
        "license": license,
        "source_url": url,
        "retrieved_at": now_iso(),
    }
    if message:
        body["message"] = message
    body.update(extra)
    return body
