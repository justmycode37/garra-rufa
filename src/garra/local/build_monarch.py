"""monarch.sqlite from the Monarch KG KGX export (monarch-kg.tar.gz), replacing the Monarch
API v3 search / entity / association endpoints.

Only association categories the code queries are kept, and only edges touching a human
gene, a disease or a phenotype (HGNC / MONDO / HP), plus the subclass hierarchy.

  node(id, name, category, description, full_name, symbol, xref, exact_synonym, in_taxon)
  edge(subject, object, category, predicate, publications, has_percentage, has_count,
       has_total, frequency_qualifier, onset_qualifier, negated, knowledge_source)
  nxref(xref, id)                    xref -> canonical node
  sub(child, parent)                 biolink:subclass_of between kept nodes
"""

from __future__ import annotations

import ast
import csv
import io
import json
import re
import sys
import tarfile
import warnings

from garra.local import RAW, finish, writer

TAR = RAW / "monarch" / "monarch-kg.tar.gz"
CATEGORIES = {
    "biolink:DiseaseToPhenotypicFeatureAssociation",
    "biolink:CausalGeneToDiseaseAssociation",
    "biolink:CorrelatedGeneToDiseaseAssociation",
    "biolink:GeneToPhenotypicFeatureAssociation",
    "biolink:ChemicalEntityToDiseaseOrPhenotypicFeatureAssociation",
    "biolink:DiseaseOrPhenotypicFeatureToGeneticInheritanceAssociation",
    "biolink:VariantToDiseaseAssociation",
    "biolink:MacromolecularMachineToBiologicalProcessAssociation",
    "biolink:MacromolecularMachineToMolecularActivityAssociation",
    "biolink:MacromolecularMachineToCellularComponentAssociation",
    "biolink:PairwiseGeneToGeneInteraction",
    "biolink:GeneToGeneHomologyAssociation",
    "biolink:GeneToExpressionSiteAssociation",
    "biolink:GeneToPathwayAssociation",
}
CANON = ("HGNC:", "MONDO:", "HP:")
_LIST = re.compile(r"'([^']*)'")


def ready() -> bool:
    return TAR.is_file()


def _list(v: str) -> list[str]:
    """KGX list cells: "['a', 'b']" or "a|b"."""
    if not v:
        return []
    if v.startswith("["):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                return [str(x) for x in ast.literal_eval(v)]
        except (ValueError, SyntaxError):
            return _LIST.findall(v)
    return [x for x in v.split("|") if x]


def _reader(tar: tarfile.TarFile, name: str):
    m = tar.getmember(name)
    f = io.TextIOWrapper(tar.extractfile(m), encoding="utf-8", newline="")
    csv.field_size_limit(1 << 30)
    return csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)


def build() -> None:
    con = writer("monarch")
    con.executescript("""
    CREATE TABLE node(id TEXT PRIMARY KEY, name TEXT, category TEXT, description TEXT,
                      full_name TEXT, symbol TEXT, xref TEXT, exact_synonym TEXT,
                      in_taxon TEXT);
    CREATE TABLE edge(subject TEXT, object TEXT, category TEXT, predicate TEXT,
                      publications TEXT, has_percentage REAL, has_count INTEGER,
                      has_total INTEGER, frequency_qualifier TEXT, onset_qualifier TEXT,
                      negated INTEGER, knowledge_source TEXT);
    CREATE TABLE sub(child TEXT, parent TEXT);
    CREATE TABLE nxref(xref TEXT, id TEXT);
    """)
    tar = tarfile.open(TAR, "r:gz")
    names = [m.name for m in tar.getmembers()]
    edges_name = next(n for n in names if n.endswith("_edges.tsv"))
    nodes_name = next(n for n in names if n.endswith("_nodes.tsv"))
    # the tar stores nodes first: read them into memory-light dict of wanted ids later, so
    # read edges first with a second handle on the archive
    tar_e = tarfile.open(TAR, "r:gz")
    rows = _reader(tar_e, edges_name)
    h = {k: i for i, k in enumerate(next(rows))}
    ref: set[str] = set()
    batch, subs, n = [], [], 0
    for r in rows:
        n += 1
        if n % 2_000_000 == 0:
            print(f"  ... {n / 1e6:.0f}M edges read", file=sys.stderr, flush=True)
        cat, s, o = r[h["category"]], r[h["subject"]], r[h["object"]]
        pred = r[h["predicate"]]
        if pred == "biolink:subclass_of":
            if s.startswith(CANON) and o.startswith(CANON):
                subs.append((s, o))
                ref.update((s, o))
            continue
        if cat not in CATEGORIES or not (s.startswith(CANON) or o.startswith(CANON)):
            continue
        ref.update((s, o))

        def num(k, f=float):
            v = r[h[k]] if k in h else ""
            try:
                return f(v) if v else None
            except ValueError:
                return None

        batch.append((s, o, cat, pred, json.dumps(_list(r[h["publications"]])) or None,
                      num("has_percentage"), num("has_count", int), num("has_total", int),
                      r[h["frequency_qualifier"]] or None, r[h["onset_qualifier"]] or None,
                      int(r[h["negated"]] == "True"), r[h["primary_knowledge_source"]] or None))
        if len(batch) >= 200_000:
            con.executemany("INSERT INTO edge VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", batch)
            batch.clear()
    con.executemany("INSERT INTO edge VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", batch)
    con.executemany("INSERT INTO sub VALUES (?,?)", subs)
    tar_e.close()
    print(f"  {n} edges read; kept {con.execute('SELECT count(*) FROM edge').fetchone()[0]} "
          f"associations, {len(subs)} subclass links", flush=True)

    rows = _reader(tar, nodes_name)
    h = {k: i for i, k in enumerate(next(rows))}
    nodes, xrefs = [], []
    for r in rows:
        nid = r[h["id"]]
        if nid not in ref and not nid.startswith(CANON):
            continue
        xr = _list(r[h["xref"]])
        nodes.append((nid, r[h["name"]] or None, r[h["category"]] or None,
                      r[h["description"]] or None, r[h["full_name"]] or None,
                      r[h["symbol"]] or None, json.dumps(xr), json.dumps(
                          _list(r[h["exact_synonym"]])), r[h["in_taxon"]] or None))
        if nid.startswith(CANON):
            xrefs += [(x, nid) for x in xr]
            xrefs += [(x, nid) for x in _list(r[h["same_as"]]) if x != nid]
    con.executemany("INSERT OR IGNORE INTO node VALUES (?,?,?,?,?,?,?,?,?)", nodes)
    con.executemany("INSERT INTO nxref VALUES (?,?)", xrefs)
    tar.close()
    print(f"  {len(nodes)} nodes, {len(xrefs)} xrefs", flush=True)
    print("  indexing ...", flush=True)
    con.executescript("""
    CREATE INDEX edge_s ON edge(subject, category);
    CREATE INDEX edge_o ON edge(object, category);
    CREATE INDEX sub_child ON sub(child);
    CREATE INDEX sub_parent ON sub(parent);
    CREATE INDEX nxref_x ON nxref(xref COLLATE NOCASE);
    CREATE VIRTUAL TABLE node_fts USING fts5(name, synonyms, id UNINDEXED);
    INSERT INTO node_fts SELECT name, exact_synonym, id FROM node
        WHERE id LIKE 'MONDO:%' OR id LIKE 'HP:%' OR id LIKE 'HGNC:%';
    """)
    finish("monarch", con)
