"""Read helpers over pubmed.sqlite (see build_pubmed.py): PubMed query translation to
FTS5, search, and stored records."""

from __future__ import annotations

import json
import re
import zlib

from garra.local import available, connect

# PubMed field tag -> FTS columns
FIELDS = {"tiab": "tiab", "title/abstract": "tiab", "ti": "tiab", "title": "tiab",
          "ab": "tiab", "abstract": "tiab", "tw": "tiab", "text word": "tiab",
          "mesh terms": "mesh", "mh": "mesh", "mesh": "mesh",
          "mesh major topic": "majr", "majr": "majr",
          "supplementary concept": "supp", "nm": "supp",
          "pt": "pt", "publication type": "pt",
          "all fields": "tiab mesh supp", "all": "tiab mesh supp"}
EXPLODE = {"mesh", "majr"}
MAX_EXPLODE = 60
_TOKEN = re.compile(r'\s*(?:(\()|(\))|"([^"]*)"\s*(?:\[([^\]]+)\])?|([^\s()"\[]+)\s*'
                    r'(?:\[([^\]]+)\])?)')
_WORD = re.compile(r"\w+", re.UNICODE)


class Unsupported(ValueError):
    pass


def db():
    return connect("pubmed")


def has(pmids) -> set[str]:
    """The PMIDs (as given) that the local subset holds."""
    if not available("pubmed"):
        return set()
    ids = [str(p) for p in pmids if str(p).isdigit()]
    out = set()
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        out |= {str(r[0]) for r in db().execute(
            f"SELECT pmid FROM paper WHERE pmid IN ({','.join('?' * len(chunk))})",
            [int(x) for x in chunk])}
    return out


def _phrase(text: str) -> str | None:
    ws = _WORD.findall(text.replace("/", " "))
    return '"' + " ".join(ws) + '"' if ws else None


def _explode(heading: str) -> list[str]:
    """A MeSH heading (optionally "/qualifier") and its narrower headings, as PubMed's
    [MeSH Terms] search does."""
    head, _, qual = heading.partition("/")
    out = [heading]
    if not available("mesh"):
        return out
    from garra.local import mesh
    uis = mesh.by_name(head, "D")
    seen, todo = set(), list(uis[:1])
    while todo and len(out) < MAX_EXPLODE:
        ui = todo.pop(0)
        for child in mesh.narrower(ui):
            if child in seen:
                continue
            seen.add(child)
            nm = mesh.name(child)
            if nm:
                out.append(f"{nm}/{qual}" if qual else nm)
            todo.append(child)
    return out[:MAX_EXPLODE]


def _term(text: str, field: str | None, quoted: bool) -> str:
    f = (field or "").strip().lower()
    if f and f not in FIELDS:
        raise Unsupported(f"field [{field}]")
    cols = FIELDS.get(f, "tiab mesh supp")
    if f in ("pt", "publication type"):
        p = _phrase(text)
        if not p:
            raise Unsupported(text)
        return f"{{{cols}}} : {p}"
    if cols in EXPLODE:
        phrases = [p for p in map(_phrase, _explode(text)) if p]
        if not phrases:
            raise Unsupported(text)
        return f"{{{cols}}} : (" + " OR ".join(phrases) + ")"
    if not quoted and text.endswith("*") and _WORD.fullmatch(text[:-1]):
        return f"{{{cols}}} : {text[:-1]}*"
    p = _phrase(text)
    if not p:
        raise Unsupported(text)
    return f"{{{cols}}} : {p}"


def to_fts(term: str) -> str:
    """PubMed boolean query -> FTS5 MATCH expression (raises Unsupported)."""
    pos, out, need_op = 0, [], False
    term = term.strip()
    while pos < len(term):
        m = _TOKEN.match(term, pos)
        if not m or m.end() == pos:
            raise Unsupported(term[pos:pos + 20])
        pos = m.end()
        lp, rp, qtext, qfield, word, wfield = m.groups()
        if lp:
            if need_op:
                out.append("AND")
            out.append("(")
            need_op = False
        elif rp:
            out.append(")")
            need_op = True
        elif word in ("AND", "OR", "NOT") and wfield is None:
            out.append(word)
            need_op = False
        else:
            if need_op:
                out.append("AND")
            out.append(_term(qtext if qtext is not None else word,
                             qfield if qtext is not None else wfield, qtext is not None))
            need_op = True
    if not out:
        raise Unsupported("empty")
    return " ".join(out)


def search(term: str, n: int, sort: str = "relevance") -> tuple[int, list[str]]:
    q = to_fts(term)
    con = db()
    count = con.execute("SELECT count(*) FROM paper_fts WHERE paper_fts MATCH ?",
                        (q,)).fetchone()[0]
    if sort == "relevance":
        order = ("bm25(paper_fts, 1.0, 2.0, 4.0, 0.1, 2.0) "
                 "- 0.02 * coalesce(p.year - 2000, 0)")
    else:
        order = "p.year DESC, p.pmid DESC"
    rows = con.execute(f"SELECT f.rowid FROM paper_fts f JOIN paper p ON p.pmid=f.rowid "
                       f"WHERE paper_fts MATCH ? ORDER BY {order} LIMIT ?", (q, n)).fetchall()
    return count, [str(r[0]) for r in rows]


def record(pmid) -> dict | None:
    row = db().execute("SELECT doc FROM paper WHERE pmid=?", (int(pmid),)).fetchone()
    return json.loads(zlib.decompress(row[0])) if row else None
