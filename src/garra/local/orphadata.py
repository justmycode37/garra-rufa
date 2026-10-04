"""Read helpers over orphadata.sqlite (see build_orphadata.py)."""

from __future__ import annotations

import json
import re

from garra.local import connect

_WORD = re.compile(r"\w+", re.UNICODE)


def db():
    return connect("orphadata")


def norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def disorder(code: str) -> dict | None:
    row = db().execute("SELECT code, name, definition, type, grp FROM disorder WHERE code=?",
                       (str(code),)).fetchone()
    return dict(zip(("code", "name", "definition", "type", "group"), row)) if row else None


def name(code: str) -> str | None:
    d = disorder(code)
    return d["name"] if d else None


def find_name(text: str) -> list[tuple[str, str]]:
    """(code, preferred term) whose preferred term or synonym equals `text`."""
    rows = db().execute("SELECT o.code, d.name FROM oname o JOIN disorder d ON d.code=o.code "
                        "WHERE o.norm=? ORDER BY o.is_pref DESC", (norm(text),)).fetchall()
    return list(dict.fromkeys(rows))


def search_names(text: str, n: int = 20) -> list[tuple[str, str]]:
    out = find_name(text)
    words = _WORD.findall(text)
    if words:
        q = " ".join(f'"{w}"' for w in words)
        for code, nm in db().execute(
                "SELECT f.code, d.name FROM oname_fts f JOIN disorder d ON d.code=f.code "
                "WHERE oname_fts MATCH ? ORDER BY bm25(oname_fts) - f.is_pref * 0.5 LIMIT ?",
                (q, n * 4)):
            if (code, nm) not in out:
                out.append((code, nm))
    return list(dict.fromkeys(out))[:n]


def by_xref(source: str, reference: str) -> list[tuple[str, str, str]]:
    """(code, preferred term, mapping relation) of disorders mapped to source:reference."""
    return db().execute("SELECT x.code, d.name, x.relation FROM oxref x "
                        "JOIN disorder d ON d.code=x.code WHERE x.source=? AND x.reference=?",
                        (source, reference)).fetchall()


def xrefs(code: str) -> list[tuple[str, str, str]]:
    return db().execute("SELECT source, reference, relation FROM oxref WHERE code=?",
                        (str(code),)).fetchall()


def phenotypes(code: str) -> list[tuple[str, str, str, str]]:
    return db().execute("SELECT hp, term, freq, criteria FROM pheno WHERE code=?",
                        (str(code),)).fetchall()


def by_phenotype(hp: str) -> list[str]:
    return [r[0] for r in db().execute("SELECT DISTINCT code FROM pheno WHERE hp=?", (hp,))]


def genes(code: str) -> list[dict]:
    return [json.loads(r[0]) for r in db().execute("SELECT assoc FROM gene WHERE code=?",
                                                   (str(code),))]


def by_gene(symbol: str) -> list[str]:
    return [r[0] for r in db().execute("SELECT DISTINCT code FROM gene WHERE symbol=?",
                                       (symbol.upper(),))]


def preferential_parent(code: str) -> str | None:
    row = db().execute("SELECT parent FROM pref_parent WHERE code=?", (str(code),)).fetchone()
    return row[0] if row else None


def preferential_children(code: str) -> list[str]:
    return [r[0] for r in db().execute("SELECT code FROM pref_parent WHERE parent=?",
                                       (str(code),))]


def classifications(code: str) -> list[dict]:
    """Per classification containing `code`: {"cls", "parents", "childs"}."""
    con = db()
    out = []
    for (cls,) in con.execute("SELECT DISTINCT cls FROM classif WHERE code=?", (str(code),)):
        parents = [r[0] for r in con.execute(
            "SELECT DISTINCT parent FROM classif WHERE code=? AND cls=? AND parent IS NOT NULL",
            (str(code), cls))]
        childs = [r[0] for r in con.execute(
            "SELECT DISTINCT code FROM classif WHERE parent=? AND cls=?", (str(code), cls))]
        out.append({"cls": cls, "parents": parents, "childs": childs})
    return out
