"""reporter.sqlite from the NIH ExPORTER bulk files already in data/reporter-nih-gov/
(src/data/reporter-nih-gov.py), replacing POST api.reporter.nih.gov/v2/projects/search
for free-text searches. Fiscal years from FIRST_FY on; ExPORTER repeats a project for
every fiscal year, so only the latest application of each core project is kept.

  project(appl_id, core, project_num, fy, title, pi, ic, ic_name, org, abstract)
  project_fts(title, abstract, appl_id)
"""

from __future__ import annotations

import csv
import html
import io
import sys
import zipfile

from garra.local import finish, writer
from garra.paths import DATA_DIR

SRC = DATA_DIR / "reporter-nih-gov"
FIRST_FY = 2015


def ready() -> bool:
    return any((SRC / "projects").glob("RePORTER_PRJ_C_FY*.zip"))


def _rows(path):
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.lower().endswith(".csv"):
                raw = z.read(n)
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = raw.decode("latin-1")
                csv.field_size_limit(1 << 30)
                yield from csv.DictReader(io.StringIO(text))


def _fy(path) -> int:
    digits = "".join(c for c in path.stem.rsplit("FY", 1)[-1] if c.isdigit())
    return int(digits[:4]) if digits else 0


def build(keep_raw: bool = True) -> None:
    con = writer("reporter")
    con.executescript("""
    CREATE TABLE project(appl_id TEXT PRIMARY KEY, core TEXT, project_num TEXT, fy INTEGER,
                         title TEXT, pi TEXT, ic TEXT, ic_name TEXT, org TEXT, abstract TEXT);
    """)
    latest: dict[str, tuple] = {}  # core project number -> newest application row
    for p in sorted((SRC / "projects").glob("RePORTER_PRJ_C_FY*.zip")):
        if _fy(p) < FIRST_FY:
            continue
        n = 0
        for r in _rows(p):
            pi = html.unescape(r.get("PI_NAMEs") or "")
            contact = next((x for x in pi.split(";") if "(contact)" in x), pi.split(";")[0])
            core = r.get("CORE_PROJECT_NUM") or r["APPLICATION_ID"]
            row = (r["APPLICATION_ID"], core, r.get("FULL_PROJECT_NUM"), int(r.get("FY") or 0),
                   html.unescape(r.get("PROJECT_TITLE") or ""),
                   contact.replace("(contact)", "").strip(), r.get("ADMINISTERING_IC"),
                   r.get("IC_NAME"), html.unescape(r.get("ORG_NAME") or ""), None)
            if core not in latest or row[3] >= latest[core][3]:
                latest[core] = row
            n += 1
        print(f"  {p.name}: {n} applications", file=sys.stderr, flush=True)
    con.executemany("INSERT OR REPLACE INTO project VALUES (?,?,?,?,?,?,?,?,?,?)",
                    latest.values())
    keep = {row[0] for row in latest.values()}
    for p in sorted((SRC / "abstracts").glob("RePORTER_PRJABS_C_FY*.zip")):
        if _fy(p) < FIRST_FY:
            continue
        con.executemany("UPDATE project SET abstract=? WHERE appl_id=?",
                        ((html.unescape(r.get("ABSTRACT_TEXT") or "").strip(),
                          r["APPLICATION_ID"]) for r in _rows(p)
                         if r["APPLICATION_ID"] in keep))
    n = con.execute("SELECT count(*) FROM project").fetchone()[0]
    print(f"  {n} projects (FY{FIRST_FY}+)", flush=True)
    con.executescript("""
    CREATE INDEX project_fy ON project(fy);
    CREATE VIRTUAL TABLE project_fts USING fts5(title, abstract, appl_id UNINDEXED);
    INSERT INTO project_fts SELECT title, coalesce(abstract, ''), appl_id FROM project;
    INSERT INTO project_fts(project_fts) VALUES ('optimize');
    """)
    finish("reporter", con)
