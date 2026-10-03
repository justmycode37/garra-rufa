"""Evidence-linked connection cards from a fetcher-supplied packet. No network I/O."""

from .builder import build_connections
from .report import render_connections

__all__ = ["build_connections", "render_connections"]
