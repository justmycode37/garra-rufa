"""Read helpers over ontology.sqlite (see build_ontology.py)."""

from __future__ import annotations

import json
import re

from garra.local import connect

OBO = "http://purl.obolibrary.org/obo/"
FMA_IRI = "http://purl.org/sig/ont/fma/fma"
HGNC_IRI = "http://identifiers.org/hgnc/"
PREFIX_OF = {"mondo": "MONDO", "doid": "DOID", "uberon": "UBERON", "cl": "CL", "go": "GO",
             "chebi": "CHEBI", "hp": "HP", "fma": "FMA"}
_FTS_WORD = re.compile(r"\w+", re.UNICODE)


def db():
    return connect("ontology")


def norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def iri(curie: str) -> str:
    p, _, local = curie.partition(":")
    if p == "FMA":
        return FMA_IRI + local
    if p == "HGNC":
        return HGNC_IRI + local
    return OBO + curie.replace(":", "_", 1)


def curie(iri_: str) -> str | None:
    if iri_.startswith(OBO):
        return iri_[len(OBO):].replace("_", ":", 1)
    if iri_.startswith(FMA_IRI):
        return "FMA:" + iri_[len(FMA_IRI):]
    if iri_.startswith(HGNC_IRI):
        return "HGNC:" + iri_[len(HGNC_IRI):]
    if re.fullmatch(r"[A-Za-z][\w.]*:\S+", iri_):
        return iri_
    return None


def term(c: str) -> dict | None:
    con = db()
    row = con.execute("SELECT curie, ont, label, definition, def_refs, comment, obsolete "
                      "FROM term WHERE curie=?", (c,)).fetchone()
    if not row:
        alt = con.execute("SELECT curie FROM alt WHERE alt_id=?", (c,)).fetchone()
        if alt:
            return term(alt[0])
        return None
    return {"curie": row[0], "ont": row[1], "label": row[2], "definition": row[3],
            "def_refs": json.loads(row[4]) if row[4] else [], "comment": row[5],
            "obsolete": bool(row[6])}


def label(c: str) -> str | None:
    row = db().execute("SELECT label FROM term WHERE curie=?", (c,)).fetchone()
    if row:
        return row[0]
    if c.startswith("HGNC:"):
        row = db().execute("SELECT symbol FROM hgnc WHERE hgnc_id=?", (c,)).fetchone()
        return row[0] if row else None
    return None


def labels(curies) -> dict[str, str]:
    out = {}
    for c in dict.fromkeys(curies):
        lab = label(c)
        if lab:
            out[c] = lab
    return out


def synonyms(c: str) -> list[tuple[str, str, str | None]]:
    """(name, scope, type) of the term's synonyms."""
    return db().execute("SELECT name, scope, type FROM name WHERE curie=? AND is_label=0",
                        (c,)).fetchall()


def xrefs(c: str) -> list[str]:
    return [r[0] for r in db().execute("SELECT xref FROM xref WHERE curie=?", (c,))]


def by_xref(x: str, ont: str | None = None) -> list[str]:
    """Terms carrying cross-reference `x` (e.g. MONDO terms with xref OMIM:154700)."""
    rows = db().execute("SELECT x.curie FROM xref x JOIN term t ON t.curie=x.curie "
                        "WHERE x.xref=? COLLATE NOCASE AND t.obsolete=0"
                        + (" AND t.ont=?" if ont else ""), (x, ont) if ont else (x,))
    return [r[0] for r in rows]


def parents(c: str) -> list[str]:
    return [r[0] for r in db().execute(
        "SELECT obj FROM edge WHERE subj=? AND rel='is_a'", (c,))]


def children(c: str) -> list[str]:
    return [r[0] for r in db().execute(
        "SELECT e.subj FROM edge e JOIN term t ON t.curie=e.subj "
        "WHERE e.obj=? AND e.rel='is_a' AND t.obsolete=0 ORDER BY t.label", (c,))]


def relations_out(c: str) -> list[tuple[str, str, str | None]]:
    """(relation, target, label from the file) for non-is_a edges."""
    return db().execute("SELECT rel, obj, obj_label FROM edge WHERE subj=? AND rel!='is_a'",
                        (c,)).fetchall()


def relations_in(c: str) -> list[tuple[str, str]]:
    return db().execute("SELECT rel, subj FROM edge WHERE obj=? AND rel!='is_a'",
                        (c,)).fetchall()


def subsets(c: str) -> list[str]:
    return [r[0] for r in db().execute("SELECT subset FROM subset WHERE curie=?", (c,))]


def annotations(c: str) -> list[tuple[str, str]]:
    return db().execute("SELECT key, value FROM annot WHERE curie=?", (c,)).fetchall()


def exact(text: str, ont: str, n: int = 1) -> list[tuple[str, str]]:
    """(curie, label) whose label or synonym equals `text` (labels first, no obsoletes)."""
    rows = db().execute(
        "SELECT n.curie, t.label FROM name n JOIN term t ON t.curie=n.curie "
        "WHERE n.norm=? AND n.ont=? AND t.obsolete=0 "
        "ORDER BY n.is_label DESC, n.scope='EXACT' DESC, length(t.label) LIMIT ?",
        (norm(text), ont, n * 4)).fetchall()
    return list(dict.fromkeys(rows))[:n]


def search(text: str, ont: str, n: int = 10, obsoletes: bool = False) -> list[tuple[str, str]]:
    """Free-text search like OLS: exact label/synonym hits first, then FTS ranking."""
    out = exact(text, ont, n)
    words = _FTS_WORD.findall(text)
    if words and len(out) < n:
        q = " ".join(f'"{w}"' for w in words)
        rows = db().execute(
            "SELECT f.curie, t.label, t.obsolete FROM name_fts f JOIN term t ON t.curie=f.curie "
            "WHERE name_fts MATCH ? AND f.ont=? ORDER BY bm25(name_fts) - f.is_label * 0.5 "
            "LIMIT ?", (q, ont, n * 5)).fetchall()
        for c, lab, obs in rows:
            if (obsoletes or not obs) and (c, lab) not in out:
                out.append((c, lab))
            if len(out) >= n:
                break
    return out


def hgnc(field: str, value: str) -> list[dict]:
    """HGNC records by symbol / alias_symbol / prev_symbol / hgnc_id / entrez / ensembl."""
    con = db()
    cols = "hgnc_id, symbol, name, entrez, ensembl, uniprot, omim, alias, prev, locus_group"
    if field in ("symbol", "alias_symbol", "prev_symbol"):
        kind = {"symbol": "symbol", "alias_symbol": "alias", "prev_symbol": "prev"}[field]
        rows = con.execute(f"SELECT {cols} FROM hgnc WHERE hgnc_id IN "
                           "(SELECT hgnc_id FROM hgnc_name WHERE norm=? AND kind=?)",
                           (value.upper(), kind)).fetchall()
    else:
        col = {"hgnc_id": "hgnc_id", "entrez_id": "entrez", "ensembl_gene_id": "ensembl"}[field]
        if col == "hgnc_id" and not value.upper().startswith("HGNC:"):
            value = f"HGNC:{value}"
        rows = con.execute(f"SELECT {cols} FROM hgnc WHERE {col}=?", (value,)).fetchall()
    keys = ["hgnc_id", "symbol", "name", "entrez_id", "ensembl_gene_id", "uniprot_ids",
            "omim_id", "alias_symbol", "prev_symbol", "locus_group"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        for k in ("uniprot_ids", "omim_id", "alias_symbol", "prev_symbol"):
            d[k] = [x for x in (d[k] or "").split("|") if x]
        out.append(d)
    return out
