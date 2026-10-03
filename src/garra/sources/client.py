"""Download an approved bulk file and refuse anything that does not check out.

The checks are the ones that failed open in the Swiss open-data rehearsal:
a documented path can 404, a published CSV can be a header-only stub, and a
redirect can leave the host that was reviewed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .catalog import REDIRECT_HOST_SUFFIXES, Source
from .envelope import envelope

USER_AGENT = "garra-rufa/0.1 (Hack-Nation rare-disease atlas)"
CHUNK = 256 * 1024


class SourceRejected(Exception):
    """The file or redirect failed a local check. Nothing is kept."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def host_allowed(hostname: str | None, source: Source, *, redirect: bool) -> bool:
    if not hostname:
        return False
    host = hostname.lower().rstrip(".")
    if host in source.hosts:
        return True
    if not redirect:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in REDIRECT_HOST_SUFFIXES)


class _CheckedRedirect(HTTPRedirectHandler):
    def __init__(self, source: Source):
        super().__init__()
        self.source = source
        self.chain: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urlparse(newurl).hostname
        if not host_allowed(host, self.source, redirect=True):
            raise SourceRejected(f"redirect host {host} is not allowlisted for {self.source.id}")
        self.chain.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _looks_valid(sample: bytes, source: Source) -> bool:
    window = sample[:8192]
    if source.expected in window:
        return True
    # phenotype.hpoa prints comments before the column header.
    if source.expected == b"database_id" and b"database_id" in sample[:65536]:
        return True
    return False


def fetch_bulk(
    source: Source,
    dest_root: Path,
    *,
    allow_large: bool = False,
    opener_factory=None,
) -> dict:
    """Stream one catalogued file into dest_root and write a sidecar manifest."""
    if source.access != "bulk":
        return envelope(
            status="rejected",
            source_id=source.id,
            publisher=source.publisher,
            license=source.license,
            url=source.url,
            message=f"{source.id} is {source.access}; it is not a bulk download",
        )
    if source.max_bytes > 100 * 1024 * 1024 and not allow_large:
        return envelope(
            status="rejected",
            source_id=source.id,
            publisher=source.publisher,
            license=source.license,
            url=source.url,
            message=f"{source.id} is over 100 MB; pass allow_large to fetch it",
        )

    start_host = urlparse(source.url).hostname
    if not host_allowed(start_host, source, redirect=False):
        return envelope(
            status="rejected",
            source_id=source.id,
            publisher=source.publisher,
            license=source.license,
            url=source.url,
            message=f"start host {start_host} is not allowlisted",
        )

    redirector = _CheckedRedirect(source)
    opener = (opener_factory or build_opener)(redirector)
    request = Request(source.url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    out_dir = dest_root / source.id
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / source.filename
    partial = final_path.with_suffix(final_path.suffix + ".part")

    try:
        with opener.open(request, timeout=120) as response:
            resolved = response.geturl()
            resolved_host = urlparse(resolved).hostname
            if not host_allowed(resolved_host, source, redirect=True):
                raise SourceRejected(f"resolved host {resolved_host} is not allowlisted")
            digest = hashlib.sha256()
            size = 0
            sample = b""
            with partial.open("wb") as handle:
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > source.max_bytes:
                        raise SourceRejected(
                            f"{source.id} exceeded {source.max_bytes} bytes; download stopped"
                        )
                    if len(sample) < 65536:
                        sample += chunk[: 65536 - len(sample)]
                    digest.update(chunk)
                    handle.write(chunk)
            status_code = getattr(response, "status", 200)
            content_type = response.headers.get("Content-Type", "")
    except SourceRejected as exc:
        partial.unlink(missing_ok=True)
        return envelope(
            status="rejected",
            source_id=source.id,
            publisher=source.publisher,
            license=source.license,
            url=source.url,
            message=exc.message,
        )
    except HTTPError as exc:
        partial.unlink(missing_ok=True)
        status = "not_found" if exc.code == 404 else "source_unavailable"
        return envelope(
            status=status,
            source_id=source.id,
            publisher=source.publisher,
            license=source.license,
            url=source.url,
            message=f"HTTP {exc.code}",
        )
    except (URLError, TimeoutError, OSError) as exc:
        partial.unlink(missing_ok=True)
        return envelope(
            status="source_unavailable",
            source_id=source.id,
            publisher=source.publisher,
            license=source.license,
            url=source.url,
            message=str(exc.reason) if isinstance(exc, URLError) and hasattr(exc, "reason") else str(exc),
        )

    if size < source.min_bytes:
        partial.unlink(missing_ok=True)
        return envelope(
            status="rejected",
            source_id=source.id,
            publisher=source.publisher,
            license=source.license,
            url=source.url,
            message=f"file is {size} bytes; expected at least {source.min_bytes}. This looks like an empty stub.",
            byte_size=size,
        )
    if not _looks_valid(sample, source):
        partial.unlink(missing_ok=True)
        return envelope(
            status="rejected",
            source_id=source.id,
            publisher=source.publisher,
            license=source.license,
            url=source.url,
            message=f"file does not contain the expected marker for {source.id}",
            byte_size=size,
        )

    partial.replace(final_path)
    record = envelope(
        status="ok",
        source_id=source.id,
        publisher=source.publisher,
        license=source.license,
        url=source.url,
        resolved_url=resolved,
        redirect_chain=redirector.chain,
        path=str(final_path),
        byte_size=size,
        sha256=digest.hexdigest(),
        http_status=status_code,
        content_type=content_type,
        role=source.role,
    )
    sidecar = out_dir / "manifest.json"
    sidecar.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    _update_index(dest_root, record)
    return record


def _update_index(dest_root: Path, record: dict) -> None:
    index_path = dest_root / "manifest.json"
    current: dict = {}
    if index_path.exists():
        current = json.loads(index_path.read_text(encoding="utf-8"))
    sources = current.get("sources", {})
    sources[record["source_id"]] = record
    current["sources"] = sources
    index_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")


def fetch_defaults(dest_root: Path, *, allow_large: bool = False, only: list[str] | None = None) -> list[dict]:
    from .catalog import BULK_SOURCES

    chosen = []
    for source in BULK_SOURCES.values():
        if only is not None:
            if source.id in only:
                chosen.append(source)
        elif source.default:
            chosen.append(source)
    return [fetch_bulk(source, dest_root, allow_large=allow_large) for source in chosen]
