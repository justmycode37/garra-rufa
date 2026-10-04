"""mesh.sqlite from the MeSH XML (descriptors + supplementary concept records), replacing
the NLM MeSH RDF API (id.nlm.nih.gov/mesh: SPARQL, lookup/descriptor, lookup/label).

  rec(ui, name, note, kind)          D = descriptor, C = supplementary concept record
  tree(ui, tn)                       descriptor tree numbers
  see_also(ui, other)                "see related" descriptors
  mterm(norm, ui, name, preferred)   every entry term
  mapped(ui, desc)                   SCR -> the descriptors it is indexed under
"""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET

from garra.local import RAW, finish, writer

DIR = RAW / "mesh"


def ready() -> bool:
    return (DIR / "desc2026.xml").is_file()


def norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _records(f, tag):
    for _, el in ET.iterparse(f, events=("end",)):
        if el.tag == tag:
            yield el
            el.clear()


def _terms(el, ui, rows):
    seen = set()
    for c in el.findall("ConceptList/Concept"):
        for t in c.findall("TermList/Term"):
            s = (t.findtext("String") or "").strip()
            if s and norm(s) not in seen:
                seen.add(norm(s))
                rows.append((norm(s), ui, s, int(t.get("RecordPreferredTermYN") == "Y")))


def build() -> None:
    con = writer("mesh")
    con.executescript("""
    CREATE TABLE rec(ui TEXT PRIMARY KEY, name TEXT, note TEXT, kind TEXT);
    CREATE TABLE tree(ui TEXT, tn TEXT);
    CREATE TABLE see_also(ui TEXT, other TEXT);
    CREATE TABLE mterm(norm TEXT, ui TEXT, name TEXT, preferred INTEGER);
    CREATE TABLE mapped(ui TEXT, descr TEXT);
    """)
    recs, trees, see, terms, mapped = [], [], [], [], []
    for el in _records(DIR / "desc2026.xml", "DescriptorRecord"):
        ui = el.findtext("DescriptorUI")
        note = None
        for c in el.findall("ConceptList/Concept"):
            if c.get("PreferredConceptYN") == "Y":
                note = (c.findtext("ScopeNote") or "").strip() or None
        recs.append((ui, el.findtext("DescriptorName/String"), note, "D"))
        trees += [(ui, t.text) for t in el.findall("TreeNumberList/TreeNumber") if t.text]
        see += [(ui, s.text) for s in el.findall(
            "SeeRelatedList/SeeRelatedDescriptor/DescriptorReferredTo/DescriptorUI") if s.text]
        _terms(el, ui, terms)
    n_desc = len(recs)
    supp = DIR / "supp2026.gz"
    if supp.is_file():
        with gzip.open(supp, "rb") as f:
            for el in _records(f, "SupplementalRecord"):
                ui = el.findtext("SupplementalRecordUI")
                recs.append((ui, el.findtext("SupplementalRecordName/String"),
                             (el.findtext("Note") or "").strip() or None, "C"))
                mapped += [(ui, d.text.lstrip("*")) for d in el.findall(
                    "HeadingMappedToList/HeadingMappedTo/DescriptorReferredTo/DescriptorUI")
                    if d.text]
                _terms(el, ui, terms)
    con.executemany("INSERT INTO rec VALUES (?,?,?,?)", recs)
    con.executemany("INSERT INTO tree VALUES (?,?)", trees)
    con.executemany("INSERT INTO see_also VALUES (?,?)", see)
    con.executemany("INSERT INTO mterm VALUES (?,?,?,?)", terms)
    con.executemany("INSERT INTO mapped VALUES (?,?)", mapped)
    print(f"  {n_desc} descriptors, {len(recs) - n_desc} supplementary records, "
          f"{len(terms)} entry terms", flush=True)
    con.executescript("""
    CREATE INDEX tree_ui ON tree(ui);
    CREATE INDEX tree_tn ON tree(tn);
    CREATE INDEX see_ui ON see_also(ui);
    CREATE INDEX see_other ON see_also(other);
    CREATE INDEX mterm_norm ON mterm(norm);
    CREATE INDEX mterm_ui ON mterm(ui);
    CREATE INDEX mapped_ui ON mapped(ui);
    """)
    finish("mesh", con)
