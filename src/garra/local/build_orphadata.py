"""orphadata.sqlite from the Orphadata XML products, replacing api.orphacode.org and
api.orphadata.com:

  disorder(code, name, definition, type, grp)       product 1 (nomenclature, definitions)
  oname(norm, code, name, is_pref)                    preferred terms and synonyms
  oxref(code, source, reference, relation)            product 1 cross-references
  pheno(code, hp, term, freq)                         product 4 (HPO phenotypes)
  gene(code, symbol, assoc)                           product 6, assoc = API-shaped JSON
  pref_parent(code, parent)                           product 7 (linearisation)
  classif(cls, code, parent)                          product 3 (all classifications)
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET

from garra.local import RAW, finish, writer

DIR = RAW / "orphadata"
_TAG = re.compile(r"<[^>]+>")


def ready() -> bool:
    return all((DIR / f).is_file() for f in ("en_product1.xml", "en_product4.xml",
                                             "en_product6.xml", "en_product7.xml"))


def norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _name(el) -> str | None:
    n = el.find("Name") if el is not None else None
    return (n.text or "").strip() if n is not None else None


def _disorders(path, tag="Disorder"):
    """Top-level <Disorder> elements (streamed)."""
    depth = 0
    for ev, el in ET.iterparse(path, events=("start", "end")):
        if el.tag == tag:
            if ev == "start":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    yield el
                    el.clear()


def _product1(con):
    dis, names, xrefs = [], [], []
    for d in _disorders(DIR / "en_product1.xml"):
        code = d.findtext("OrphaCode")
        name = (d.findtext("Name") or "").strip()
        definition = None
        for ts in d.iter("TextSection"):
            if _name(ts.find("TextSectionType")) == "Definition":
                definition = html.unescape(_TAG.sub("", html.unescape(ts.findtext("Contents")
                                                                      or ""))).strip() or None
        dis.append((code, name, definition, _name(d.find("DisorderType")),
                    _name(d.find("DisorderGroup"))))
        names.append((norm(name), code, name, 1))
        for s in d.findall("SynonymList/Synonym"):
            if s.text:
                names.append((norm(s.text), code, s.text.strip(), 0))
        for x in d.findall("ExternalReferenceList/ExternalReference"):
            xrefs.append((code, x.findtext("Source"), x.findtext("Reference"),
                          _name(x.find("DisorderMappingRelation"))))
    con.executemany("INSERT INTO disorder VALUES (?,?,?,?,?)", dis)
    con.executemany("INSERT INTO oname VALUES (?,?,?,?)", names)
    con.executemany("INSERT INTO oxref VALUES (?,?,?,?)", xrefs)
    return len(dis)


def _product4(con):
    rows = []
    for d in _disorders(DIR / "en_product4.xml"):
        code = d.findtext("OrphaCode")
        for a in d.findall("HPODisorderAssociationList/HPODisorderAssociation"):
            rows.append((code, a.findtext("HPO/HPOId"), a.findtext("HPO/HPOTerm"),
                         _name(a.find("HPOFrequency")),
                         _name(a.find("DiagnosticCriteria"))))
    con.executemany("INSERT INTO pheno VALUES (?,?,?,?,?)", rows)
    return len(rows)


def _gene_json(g) -> dict:
    return {"Symbol": g.findtext("Symbol"), "name": _name(g),
            "GeneType": _name(g.find("GeneType")),
            "Synonym": [s.text for s in g.findall("SynonymList/Synonym") if s.text],
            "ExternalReference": [{"Source": x.findtext("Source"),
                                   "Reference": x.findtext("Reference")}
                                  for x in g.findall("ExternalReferenceList/ExternalReference")],
            "Locus": [{"GeneLocus": loc.findtext("GeneLocus"),
                       "LocusKey": loc.findtext("LocusKey")}
                      for loc in g.findall("LocusList/Locus")]}


def _product6(con):
    rows = []
    for d in _disorders(DIR / "en_product6.xml"):
        code = d.findtext("OrphaCode")
        for a in d.findall("DisorderGeneAssociationList/DisorderGeneAssociation"):
            g = a.find("Gene")
            assoc = {"SourceOfValidation": a.findtext("SourceOfValidation"),
                     "Gene": _gene_json(g),
                     "DisorderGeneAssociationType": _name(a.find("DisorderGeneAssociationType")),
                     "DisorderGeneAssociationStatus":
                         _name(a.find("DisorderGeneAssociationStatus"))}
            rows.append((code, (g.findtext("Symbol") or "").upper(), json.dumps(assoc)))
    con.executemany("INSERT INTO gene VALUES (?,?,?)", rows)
    return len(rows)


def _product7(con):
    rows = []
    for d in _disorders(DIR / "en_product7.xml"):
        code = d.findtext("OrphaCode")
        for a in d.findall("DisorderDisorderAssociationList/DisorderDisorderAssociation"):
            # entries whose target is this disorder itself (no OrphaCode) list a child
            if _name(a.find("DisorderDisorderAssociationType")) == "Preferential parent"                     and a.find("TargetDisorder/OrphaCode") is not None:
                rows.append((code, a.findtext("TargetDisorder/OrphaCode")))
    con.executemany("INSERT INTO pref_parent VALUES (?,?)", rows)
    return len(rows)


def _product3(con):
    rows = []
    for path in sorted(DIR.glob("en_product3_*.xml")):
        root = ET.parse(path).getroot()
        for cls in root.iter("Classification"):
            cid = cls.get("id")

            def walk(node, parent):
                code = node.findtext("Disorder/OrphaCode")
                rows.append((cid, code, parent))
                for ch in node.findall("ClassificationNodeChildList/ClassificationNode"):
                    walk(ch, code)

            for top in cls.findall("ClassificationNodeRootList/ClassificationNode"):
                walk(top, None)
    con.executemany("INSERT INTO classif VALUES (?,?,?)", rows)
    return len(rows)


def build() -> None:
    con = writer("orphadata")
    con.executescript("""
    CREATE TABLE disorder(code TEXT PRIMARY KEY, name TEXT, definition TEXT, type TEXT,
                          grp TEXT);
    CREATE TABLE oname(norm TEXT, code TEXT, name TEXT, is_pref INTEGER);
    CREATE TABLE oxref(code TEXT, source TEXT, reference TEXT, relation TEXT);
    CREATE TABLE pheno(code TEXT, hp TEXT, term TEXT, freq TEXT, criteria TEXT);
    CREATE TABLE gene(code TEXT, symbol TEXT, assoc TEXT);
    CREATE TABLE pref_parent(code TEXT, parent TEXT);
    CREATE TABLE classif(cls TEXT, code TEXT, parent TEXT);
    """)
    print(f"  product1: {_product1(con)} disorders", flush=True)
    print(f"  product4: {_product4(con)} phenotype rows", flush=True)
    print(f"  product6: {_product6(con)} gene rows", flush=True)
    print(f"  product7: {_product7(con)} preferential parents", flush=True)
    print(f"  product3: {_product3(con)} classification nodes", flush=True)
    con.executescript("""
    CREATE VIRTUAL TABLE oname_fts USING fts5(name, code UNINDEXED, is_pref UNINDEXED);
    INSERT INTO oname_fts SELECT name, code, is_pref FROM oname;
    CREATE INDEX oname_norm ON oname(norm);
    CREATE INDEX oname_code ON oname(code);
    CREATE INDEX oxref_code ON oxref(code);
    CREATE INDEX oxref_ref ON oxref(source, reference);
    CREATE INDEX pheno_code ON pheno(code);
    CREATE INDEX pheno_hp ON pheno(hp);
    CREATE INDEX gene_code ON gene(code);
    CREATE INDEX gene_symbol ON gene(symbol);
    CREATE INDEX pref_code ON pref_parent(code);
    CREATE INDEX pref_parent_idx ON pref_parent(parent);
    CREATE INDEX classif_code ON classif(code);
    CREATE INDEX classif_parent ON classif(parent, cls);
    """)
    finish("orphadata", con)
