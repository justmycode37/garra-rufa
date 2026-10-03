"""PrimeKG precision-medicine knowledge graph (Chandak et al. 2023, Harvard Dataverse).

Dataset: https://doi.org/10.7910/DVN/IXA7BM  (~8.1M edges, ~130k nodes: diseases,
genes/proteins, drugs, phenotypes, pathways, anatomy, GO terms, exposures).
We download only `nodes.csv` (8 MB) and `edges.csv` (~387 MB, ints only) once into
<repo>/data/primekg/ (gitignored), with progress on stderr, then build a SQLite
index (primekg.sqlite) on first use so queries are fast. The first query therefore
takes a few minutes; afterwards queries are milliseconds. Pure stdlib.

Node IDs are mapped to CURIEs: MONDO:, HP:, NCBIGene:, DrugBank:, GO:, UBERON:,
Reactome:, CTD:. Relations are PrimeKG's display relations (indication,
contraindication, target, ppi, associated with, phenotype present, ...).
"""
import csv
import sqlite3
import sys
import threading
from pathlib import Path

from .base import Edge, Node, Source

BASE = "https://dataverse.harvard.edu/api/access/datafile"
FILES = {"nodes.csv": 6180617, "edges.csv": 6180616}
DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "primekg"
DB = DATA_DIR / "primekg.sqlite"

KINDS = {"gene/protein": "gene", "drug": "drug", "disease": "disease",
         "effect/phenotype": "phenotype", "anatomy": "anatomy",
         "biological_process": "process", "molecular_function": "function",
         "cellular_component": "component", "exposure": "exposure", "pathway": "pathway"}
PREFIX = {"NCBI": ("NCBIGene", 0), "MONDO": ("MONDO", 7), "MONDO_grouped": ("MONDO", 7),
          "HPO": ("HP", 7), "DrugBank": ("DrugBank", 0), "GO": ("GO", 7),
          "UBERON": ("UBERON", 7), "CTD": ("CTD", 0), "REACTOME": ("Reactome", 0)}
RELATION_NAMES = {"ppi": "protein_interacts_with", "associated with": "gene_associated",
                  "phenotype present": "has_phenotype", "phenotype absent": "lacks_phenotype",
                  "parent-child": "parent_child", "indication": "indicated_for",
                  "contraindication": "contraindicated_for", "off-label use": "off_label_use",
                  "synergistic interaction": "synergistic_with",
                  "interacts with": "interacts_with", "linked to": "linked_to",
                  "expression present": "expressed_in", "expression absent": "not_expressed_in"}


def _curie(source: str, node_id: str) -> tuple[str, tuple[str, ...]]:
    pre, pad = PREFIX.get(source, (source, 0))
    ids = node_id.split("_") if source == "MONDO_grouped" else [node_id]
    cur = [f"{pre}:{i.zfill(pad)}" for i in ids]
    return cur[0], tuple(cur[1:])


class PrimeKGSource(Source):
    name = "primekg"
    # the prefixes of _curie(); HGNC/SYMBOL genes are matched by exact gene symbol
    id_prefixes = frozenset({p.upper() for p, _ in PREFIX.values()} | {"HGNC", "SYMBOL"})
    by_name = True

    def __init__(self):
        super().__init__()
        self._db: sqlite3.Connection | None = None
        self._failed = False
        self._lock = threading.Lock()

    # -- data preparation ------------------------------------------------
    def _download(self, fname: str) -> Path:
        path = DATA_DIR / fname
        if path.exists():
            return path
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(path.suffix + ".part")
        url = f"{BASE}/{FILES[fname]}"
        with self.session.get(url, params={"format": "original"}, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            done, last = 0, -1
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    pct = int(done * 100 / total) if total else done >> 24
                    if pct != last and (pct % 5 == 0 or not total):
                        last = pct
                        print(f"[primekg] downloading {fname}: {done / 1e6:.0f}"
                              f"/{total / 1e6:.0f} MB", file=sys.stderr, flush=True)
        part.replace(path)
        return path

    def _ensure_db(self) -> sqlite3.Connection:
        if self._db is not None:
            return self._db
        if not DB.exists():
            nodes_csv, edges_csv = self._download("nodes.csv"), self._download("edges.csv")
            print("[primekg] building SQLite index (one-time, a few minutes)...",
                  file=sys.stderr, flush=True)
            tmp = DB.with_suffix(".tmp")
            tmp.unlink(missing_ok=True)
            con = sqlite3.connect(tmp)
            con.executescript("""
                CREATE TABLE nodes(idx INTEGER PRIMARY KEY, curie TEXT, alt TEXT, type TEXT,
                                   name TEXT, name_lc TEXT, source TEXT);
                CREATE TABLE edges(x INTEGER, y INTEGER, rel TEXT);""")
            with open(nodes_csv, newline="", encoding="utf-8") as f:
                rows = []
                for r in csv.DictReader(f):
                    cur, alt = _curie(r["node_source"], r["node_id"])
                    rows.append((int(r["node_index"]), cur, " ".join(alt),
                                 KINDS.get(r["node_type"], r["node_type"]), r["node_name"],
                                 r["node_name"].lower(), r["node_source"]))
                con.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?)", rows)
            with open(edges_csv, newline="", encoding="utf-8") as f:
                rd = csv.reader(f)
                next(rd)
                batch = []
                for rel, disp, x, y in rd:
                    batch.append((int(x), int(y), disp or rel))
                    if len(batch) >= 500_000:
                        con.executemany("INSERT INTO edges VALUES (?,?,?)", batch)
                        batch = []
                con.executemany("INSERT INTO edges VALUES (?,?,?)", batch)
            con.executescript("""
                CREATE INDEX edges_x ON edges(x);
                CREATE INDEX nodes_curie ON nodes(curie);
                CREATE INDEX nodes_name ON nodes(name_lc);""")
            con.commit()
            con.close()
            tmp.replace(DB)
            print("[primekg] index ready", file=sys.stderr, flush=True)
        self._db = sqlite3.connect(DB, check_same_thread=False)
        return self._db

    # -- query -----------------------------------------------------------
    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        if self._failed:
            return []
        try:
            with self._lock:
                db = self._ensure_db()
                return self._query(db, node, limit)
        except Exception as e:
            if self._db is None:  # setup failure: don't retry a huge download every node
                self._failed = True
                print(f"[primekg] disabled: {e!r}", file=sys.stderr)
            return []

    def _mk(self, row) -> Node:
        idx, curie, alt, kind, name = row[:5]
        return Node(name, id=curie, kind=kind, source=self.name,
                    xrefs=tuple(alt.split()) if alt else ())

    def _query(self, db, node: Node, limit: int) -> list[Edge]:
        cols = "idx, curie, alt, type, name"
        if node.id is None:
            return self._search(db, node, limit)
        row = None
        for cand in self.ids_for(node):
            if cand.split(":", 1)[0].upper() in ("HGNC", "SYMBOL"):
                # PrimeKG has no HGNC ids; gene nodes are named by their (unique) symbol
                symbol = node.label if cand.startswith("HGNC") else cand.split(":", 1)[1]
                row = db.execute(f"SELECT {cols} FROM nodes WHERE name_lc=? AND type='gene'",
                                 (symbol.lower(),)).fetchone()
            else:
                row = db.execute(f"SELECT {cols} FROM nodes WHERE curie=? OR (alt != '' AND "
                                 "(' '||alt||' ') LIKE ?) LIMIT 1",
                                 (cand, f"% {cand} %")).fetchone()
            if row:
                return self._relations(db, node, row, limit)
        return []

    def _search(self, db, node: Node, limit: int) -> list[Edge]:
        q = node.label.lower()
        rows = db.execute(
            "SELECT idx, curie, alt, type, name FROM nodes WHERE name_lc LIKE ? "
            "ORDER BY (name_lc=?) DESC, (name_lc LIKE ?) DESC, "
            "(type='disease') DESC, (type='effect/phenotype' OR type='phenotype') DESC, "
            "length(name_lc) LIMIT ?", (f"%{q}%", q, f"{q}%", limit)).fetchall()
        return [Edge(node, self._mk(r), "matches", self.name) for r in rows]

    def _relations(self, db, node: Node, row, limit: int) -> list[Edge]:
        src = Node(node.label if node.id else row[4], id=row[1], kind=row[3],
                   source=node.source, xrefs=tuple(dict.fromkeys((*node.xrefs, *self._mk(row).xrefs))))
        rows = db.execute(
            "SELECT e.rel, n.idx, n.curie, n.alt, n.type, n.name FROM edges e "
            "JOIN nodes n ON n.idx = e.y WHERE e.x=? LIMIT 5000", (row[0],)).fetchall()
        groups: dict[str, list] = {}
        for rel, *n in rows:
            groups.setdefault(rel, []).append(n)
        # round-robin across relation types so one huge group (e.g. ppi) can't crowd out others
        edges: list[Edge] = []
        i = 0
        while len(edges) < limit and any(len(g) > i for g in groups.values()):
            for rel, g in groups.items():
                if len(g) > i and len(edges) < limit:
                    name = RELATION_NAMES.get(rel, rel.replace(" ", "_"))
                    if src.kind == "phenotype":  # edge stored disease->phenotype semantics
                        name = {"has_phenotype": "phenotype_of",
                                "lacks_phenotype": "phenotype_absent_in"}.get(name, name)
                    elif src.kind == "disease" and g[i][3] == "drug":  # stored drug->disease
                        name = {"indicated_for": "treated_by",
                                "contraindicated_for": "contraindicated_drug",
                                "off_label_use": "off_label_drug"}.get(name, name)
                    edges.append(Edge(src, self._mk(g[i]), name, self.name))
            i += 1
        return edges
