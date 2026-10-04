"""Read helpers over mesh.sqlite (see build_mesh.py)."""

from __future__ import annotations

from garra.local import connect


def db():
    return connect("mesh")


def norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def record(ui: str) -> dict | None:
    row = db().execute("SELECT ui, name, note, kind FROM rec WHERE ui=?", (ui,)).fetchone()
    return dict(zip(("ui", "name", "note", "kind"), row)) if row else None


def name(ui: str) -> str | None:
    r = record(ui)
    return r["name"] if r else None


def trees(ui: str) -> list[str]:
    return [r[0] for r in db().execute("SELECT tn FROM tree WHERE ui=?", (ui,))]


def by_tree(tn: str) -> str | None:
    row = db().execute("SELECT ui FROM tree WHERE tn=?", (tn,)).fetchone()
    return row[0] if row else None


def broader(ui: str) -> list[str]:
    out = []
    for tn in trees(ui):
        if "." in tn:
            p = by_tree(tn.rsplit(".", 1)[0])
            if p and p not in out:
                out.append(p)
    return out


def narrower(ui: str) -> list[str]:
    out = []
    for tn in trees(ui):
        for (child,) in db().execute(
                "SELECT ui FROM tree WHERE tn LIKE ? AND tn NOT LIKE ?",
                (tn + ".%", tn + ".%.%")):
            if child not in out:
                out.append(child)
    return out


def see_also(ui: str) -> list[str]:
    con = db()
    out = [r[0] for r in con.execute("SELECT other FROM see_also WHERE ui=?", (ui,))]
    out += [r[0] for r in con.execute("SELECT ui FROM see_also WHERE other=?", (ui,))]
    return list(dict.fromkeys(out))


def by_name(text: str, kind: str | None = None) -> list[str]:
    """Records whose entry term equals `text` (preferred terms first)."""
    rows = db().execute(
        "SELECT t.ui FROM mterm t JOIN rec r ON r.ui=t.ui WHERE t.norm=?"
        + (" AND r.kind=?" if kind else "") + " ORDER BY t.preferred DESC",
        (norm(text), kind) if kind else (norm(text),))
    return list(dict.fromkeys(r[0] for r in rows))


def terms(ui: str) -> list[str]:
    return [r[0] for r in db().execute("SELECT name FROM mterm WHERE ui=?", (ui,))]


def mapped_to(ui: str) -> list[str]:
    return [r[0] for r in db().execute("SELECT descr FROM mapped WHERE ui=?", (ui,))]
