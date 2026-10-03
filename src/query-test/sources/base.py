"""Shared types for dataset sources.

Every dataset lives in its own module in this package and exposes a subclass of
Source. The main script only knows about Node, Edge and Source.query(); all
dataset-specific logic (APIs, ID formats, downloads, parsing) stays in the module.
"""
from dataclasses import dataclass, field

import requests

TIMEOUT = 30
USER_AGENT = "garra-rufa-query-test/0.1"


@dataclass(frozen=True)
class Node:
    """An entity. `id` is a CURIE (e.g. "MONDO:0007739", "HP:0001250", "OMIM:143100")
    when known, or None for a free-text term that has not been resolved yet."""
    label: str
    id: str | None = None
    kind: str = "unknown"  # disease | phenotype | gene | drug | ... | term
    source: str = "input"  # name of the Source that produced this node
    xrefs: tuple[str, ...] = field(default=(), compare=False)  # equivalent CURIEs

    def key(self) -> str:
        return self.id or f"term:{self.label.lower()}"

    def __str__(self) -> str:
        return f"[{self.kind}] {self.label} ({self.id or '-'}) <{self.source}>"


@dataclass(frozen=True)
class Edge:
    src: Node
    dst: Node
    relation: str  # e.g. "subclass_of", "has_phenotype", "gene_associated", "matches"
    source: str


class Source:
    """Base class for a dataset. Subclasses set `name` and implement `query`."""
    name = "base"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        """Return edges from `node` to related nodes found in this dataset.

        Must accept both free-text nodes (id None -> search by label) and CURIE
        nodes (look up directly if the prefix/xrefs are understood, else fall back
        to label search or return []). Return at most ~`limit` edges. Never raise
        on network/data errors; return [] instead.
        """
        raise NotImplementedError

    def get_json(self, url: str, **kwargs):
        r = self.session.get(url, timeout=TIMEOUT, **kwargs)
        r.raise_for_status()
        return r.json()
