"""Local copies of the upstream datasets, so queries do not hit the network.

Layout (gitignored, under <repo>/data/local/):
  raw/<group>/...      files as downloaded (download.py)
  <name>.sqlite        indexes built from them (build_*.py)

Nothing here is required: every reader returns None / [] when its index is missing,
and callers fall back to the live API. `python -m garra.local status` shows what exists.

  PYTHONPATH=src python -m garra.local download            # all groups
  PYTHONPATH=src python -m garra.local build               # all indexes whose raw files exist
  PYTHONPATH=src python -m garra.local status
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from garra.paths import DATA_DIR

LOCAL_DIR = DATA_DIR / "local"
RAW = LOCAL_DIR / "raw"

_tls = threading.local()


def db_path(name: str) -> Path:
    return LOCAL_DIR / f"{name}.sqlite"


def available(name: str) -> bool:
    return db_path(name).is_file()


def connect(name: str) -> sqlite3.Connection | None:
    """Read-only connection to data/local/<name>.sqlite, one per thread; None if absent."""
    conns = getattr(_tls, "conns", None)
    if conns is None:
        conns = _tls.conns = {}
    if name in conns:
        return conns[name]
    path = db_path(name)
    if not path.is_file():
        return None
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True,
                          check_same_thread=False)
    con.execute("PRAGMA query_only = 1")
    con.execute("PRAGMA cache_size = -65536")
    con.execute("PRAGMA mmap_size = 1073741824")
    conns[name] = con
    return con


def close_all() -> None:
    """Close this thread's read connections (e.g. before replacing an index file)."""
    for con in (getattr(_tls, "conns", None) or {}).values():
        con.close()
    _tls.conns = {}


def writer(name: str) -> sqlite3.Connection:
    """Fresh database for a build: written to <name>.sqlite.tmp, see finish()."""
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = db_path(name).with_suffix(".sqlite.tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")
    con.execute("PRAGMA cache_size = -1048576")
    con.execute("PRAGMA temp_store = MEMORY")
    return con


def finish(name: str, con: sqlite3.Connection) -> Path:
    """Analyze, close and move the .tmp build into place."""
    con.commit()
    con.execute("ANALYZE")
    con.close()
    final = db_path(name)
    tmp = final.with_suffix(".sqlite.tmp")
    final.unlink(missing_ok=True)
    tmp.rename(final)
    return final
