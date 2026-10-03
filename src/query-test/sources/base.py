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
    """Base class for a dataset. Subclasses set `name`, `id_prefixes`, `by_name` and
    implement `query`.

    NOTE FOR FUTURE AGENTS: every Source must declare how it can be queried, and
    main.run() only sends it nodes that match:
      id_prefixes  CURIE prefixes (upper case) this source looks up directly. A node is
                   sent here when its id or any xref has one of these prefixes.
      by_name      True if the source can free-text search a label. Only nodes with no
                   id at all (the raw input term) are ever name-searched.
    query() must follow the same rule: never fall back to a label search for a node
    that has an id. Fuzzy label matches ("Marfan syndrome" -> "Sick sinus syndrome",
    "Loeys-Dietz syndrome 2" -> "Tietz syndrome") were the main source of off-topic
    nodes. If a lookup by id fails, return [] instead.
    """
    name = "base"
    id_prefixes: frozenset[str] = frozenset()
    by_name: bool = False

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def ids_for(self, node: Node) -> list[str]:
        """The node's id/xrefs this source understands, in order of preference."""
        return [c for c in (node.id, *node.xrefs)
                if c and c.split(":", 1)[0].upper() in self.id_prefixes]

    def accepts(self, node: Node) -> bool:
        """Whether main.run() should send `node` to this source (see class docstring)."""
        return self.by_name if node.id is None else bool(self.ids_for(node))

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        """Return edges from `node` to related nodes found in this dataset.

        Free-text node (id None): search by label. CURIE node: look up through
        ids_for(node), or return [] (no label fallback). Return at most ~`limit`
        edges. Never raise on network/data errors; return [] instead.
        """
        raise NotImplementedError

    def get_json(self, url: str, **kwargs):
        r = self.session.get(url, timeout=TIMEOUT, **kwargs)
        r.raise_for_status()
        return r.json()
