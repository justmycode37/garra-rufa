"""ontology.sqlite: OBO ontologies (Mondo, DOID, Uberon, CL, GO, ChEBI lite, HP), FMA (OWL)
and the HGNC gene table, replacing the EBI OLS4 API, rest.genenames.org and the HPO term
endpoints of the JAX API.

  term(curie, ont, label, definition, def_refs, comment, obsolete)
  name(ont, norm, curie, scope, type, is_label)   exact label / synonym lookup
  name_fts(name, curie, ont)                      free-text search (FTS5)
  xref(curie, xref)                               database cross-references
  edge(subj, rel, obj, obj_label)                 is_a and relationship lines
  subset(curie, subset), alt(alt_id, curie), annot(curie, key, value)
  hgnc(hgnc_id, symbol, name, entrez, ensembl, uniprot, omim, alias, prev, locus_group)
  hgnc_name(norm, hgnc_id, kind)                  symbol / alias / previous symbol
"""

from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET

from garra.local import RAW, finish, writer

OBO_FILES = {"mondo": "mondo.obo", "doid": "doid.obo", "uberon": "uberon.obo", "cl": "cl.obo",
             "go": "go.obo", "chebi": "chebi_lite.obo", "hp": "hp.obo"}
FMA_IRI = "http://purl.org/sig/ont/fma/"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
RDFS = "{http://www.w3.org/2000/01/rdf-schema#}"
OWL = "{http://www.w3.org/2002/07/owl#}"
FMA = "{http://purl.org/sig/ont/fma/}"
XMLNS = "{http://www.w3.org/XML/1998/namespace}"

_QUOTED = re.compile(r'^"((?:[^"\\]|\\.)*)"\s*(.*)$')
_SYN = re.compile(r'^(EXACT|NARROW|BROAD|RELATED)\s*([^\[\s]*)\s*\[(.*?)\]')
_IDORG = re.compile(r"^https?://identifiers\.org/([a-z]+)/(.+)$")


def norm(s: str) -> str:
    return " ".join(s.lower().split())


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def curie_of(x: str) -> str:
    m = _IDORG.match(x)
    if m:
        return f"{m[1].upper()}:{m[2]}"
    if x.startswith("http://purl.obolibrary.org/obo/"):
        return x.rsplit("/", 1)[1].replace("_", ":", 1)
    return x


def _unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\n", " ").replace("\\\\", "\\")


def ready() -> bool:
    d = RAW / "ontology"
    return all((d / f).is_file() for f in OBO_FILES.values()) and \
        (d / "hgnc_complete_set.txt").is_file()


def _schema(con):
    con.executescript("""
    CREATE TABLE term(curie TEXT PRIMARY KEY, ont TEXT, label TEXT, definition TEXT,
                      def_refs TEXT, comment TEXT, obsolete INTEGER);
    CREATE TABLE name(ont TEXT, norm TEXT, curie TEXT, name TEXT, scope TEXT, type TEXT,
                      is_label INTEGER);
    CREATE TABLE xref(curie TEXT, xref TEXT);
    CREATE TABLE edge(subj TEXT, rel TEXT, obj TEXT, obj_label TEXT);
    CREATE TABLE subset(curie TEXT, subset TEXT);
    CREATE TABLE alt(alt_id TEXT, curie TEXT);
    CREATE TABLE annot(curie TEXT, key TEXT, value TEXT);
    CREATE VIRTUAL TABLE name_fts USING fts5(name, curie UNINDEXED, ont UNINDEXED,
                                             is_label UNINDEXED, tokenize='unicode61');
    CREATE TABLE hgnc(hgnc_id TEXT PRIMARY KEY, symbol TEXT, name TEXT, entrez TEXT,
                      ensembl TEXT, uniprot TEXT, omim TEXT, alias TEXT, prev TEXT,
                      locus_group TEXT, status TEXT);
    CREATE TABLE hgnc_name(norm TEXT, hgnc_id TEXT, kind TEXT);
    """)


def _load_obo(con, ont: str, path) -> int:
    typedefs: dict[str, str] = {}
    stanza: dict | None = None
    kind = None
    rows = {"term": [], "name": [], "xref": [], "edge": [], "subset": [], "alt": [],
            "annot": []}
    n = 0

    def flush():
        nonlocal n
        if kind == "Typedef" and stanza and "id" in stanza:
            typedefs[stanza["id"]] = stanza.get("name", stanza["id"])
        if kind != "Term" or not stanza or "id" not in stanza:
            return
        c = stanza["id"]
        if c.split(":", 1)[0].upper() != ont.upper():
            return  # imported class (e.g. a CL term inside uberon.obo): its own file has it
        n += 1
        rows["term"].append((c, ont, stanza.get("name") or c, stanza.get("def"),
                             json.dumps(stanza.get("def_refs") or []) if stanza.get("def_refs")
                             else None, stanza.get("comment"), int(stanza.get("obsolete", 0))))
        if stanza.get("name"):
            rows["name"].append((ont, norm(stanza["name"]), c, stanza["name"], "LABEL", None, 1))
        for name, scope, typ in stanza.get("syn", []):
            rows["name"].append((ont, norm(name), c, name, scope, typ, 0))
        rows["xref"] += [(c, x) for x in stanza.get("xref", [])]
        rows["edge"] += [(c, r, o, lab) for r, o, lab in stanza.get("rel", [])]
        rows["subset"] += [(c, s) for s in stanza.get("subset", [])]
        rows["alt"] += [(a, c) for a in stanza.get("alt", [])]
        rows["annot"] += [(c, k, v) for k, v in stanza.get("annot", [])]

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("["):
                flush()
                kind = line.strip("[]")
                stanza = {}
                continue
            if stanza is None or ": " not in line:
                continue
            k, v = line.split(": ", 1)
            if k == "id":
                stanza["id"] = v.strip()
            elif k == "name":
                stanza["name"] = v.strip()
            elif k == "def":
                m = _QUOTED.match(v)
                if m:
                    stanza["def"] = _unescape(m[1])
                    refs = re.match(r"\[(.*?)\]", m[2])
                    if refs and refs[1].strip():
                        # OBO escapes ("url:https\://...") -> plain references
                        stanza["def_refs"] = [x.strip().replace("\\:", ":").removeprefix("url:")
                                              for x in refs[1].split(",") if x.strip()]
            elif k == "comment":
                stanza["comment"] = v
            elif k == "synonym":
                m = _QUOTED.match(v)
                if m:
                    s = _SYN.match(m[2])
                    scope, typ = (s[1], s[2] or None) if s else ("RELATED", None)
                    stanza.setdefault("syn", []).append((_unescape(m[1]), scope, typ))
            elif k == "xref":
                x = v.split(" ", 1)[0].strip()
                if x:
                    stanza.setdefault("xref", []).append(x)
            elif k == "is_a":
                target, _, lab = v.partition(" ! ")
                stanza.setdefault("rel", []).append(
                    ("is_a", target.split(" {", 1)[0].strip(), lab.strip() or None))
            elif k == "relationship":
                body, _, lab = v.partition(" ! ")
                parts = body.split(" {", 1)[0].split()
                if len(parts) >= 2:
                    stanza.setdefault("rel", []).append(
                        (parts[0], curie_of(parts[1]), lab.strip() or None))
            elif k == "subset":
                stanza.setdefault("subset", []).append(v.split(" ", 1)[0])
            elif k == "alt_id":
                stanza.setdefault("alt", []).append(v.strip())
            elif k == "is_obsolete" and v.strip() == "true":
                stanza["obsolete"] = 1
            elif k == "property_value":
                parts = v.split(" ", 1)
                if len(parts) == 2:
                    m = _QUOTED.match(parts[1])
                    val = m[1] if m else parts[1].split(" ")[0]
                    stanza.setdefault("annot", []).append((parts[0], val))
            elif k == "replaced_by":
                stanza.setdefault("annot", []).append(("replaced_by", v.strip()))
    flush()
    # relationship ids -> typedef names ("BFO:0000050" -> "part_of")
    rows["edge"] = [(s, r if r == "is_a" else slug(typedefs.get(r, r)), o, lab)
                    for s, r, o, lab in rows["edge"]]
    for table, rs in rows.items():
        if rs:
            q = ",".join("?" * len(rs[0]))
            con.executemany(f"INSERT INTO {table} VALUES ({q})", rs)
    return n


def _fma_curie(iri: str | None) -> str | None:
    if iri and iri.startswith(FMA_IRI + "fma"):
        return "FMA:" + iri[len(FMA_IRI) + 3:]
    return None


def _load_fma(con, path) -> int:
    """owl:Class elements: label, synonyms/definition annotations, named parents and
    simple someValuesFrom restrictions (relation = the property's local name)."""
    n = 0
    terms, names, edges = [], [], []
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag != OWL + "Class" or el.get(RDF + "about") is None:
            continue
        c = _fma_curie(el.get(RDF + "about"))
        if not c:
            el.clear()
            continue
        label = None
        for lab in el.findall(RDFS + "label"):
            if lab.get(XMLNS + "lang") in (None, "en"):
                label = (lab.text or "").strip()
                break
        definition = (el.findtext(FMA + "definition") or "").strip() or None
        terms.append((c, "fma", label or c, definition, None, None, 0))
        if label:
            names.append(("fma", norm(label), c, label, "LABEL", None, 1))
        for tag in ("synonym", "Synonym"):
            for s in el.findall(FMA + tag):
                txt = (s.text or "").strip()
                if txt:
                    names.append(("fma", norm(txt), c, txt, "EXACT", None, 0))
        for sc in el.findall(RDFS + "subClassOf"):
            res = sc.get(RDF + "resource")
            if res:
                p = _fma_curie(res)
                if p:
                    edges.append((c, "is_a", p, None))
                continue
            r = sc.find(OWL + "Restriction")
            if r is None:
                continue
            on = r.find(OWL + "onProperty")  # (an Element without children is falsy)
            prop = (on.get(RDF + "resource") or "") if on is not None else ""
            some = r.find(OWL + "someValuesFrom")
            target = _fma_curie(some.get(RDF + "resource")) if some is not None else None
            if target and prop.startswith(FMA_IRI):
                edges.append((c, slug(prop[len(FMA_IRI):]), target, None))
        n += 1
        el.clear()
    con.executemany("INSERT INTO term VALUES (?,?,?,?,?,?,?)", terms)
    con.executemany("INSERT INTO name VALUES (?,?,?,?,?,?,?)", names)
    con.executemany("INSERT INTO edge VALUES (?,?,?,?)", edges)
    return n


def _load_hgnc(con, path) -> int:
    rows, names = [], []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            hid = r["hgnc_id"]
            alias = r.get("alias_symbol") or ""
            prev = r.get("prev_symbol") or ""
            rows.append((hid, r["symbol"], r["name"], r.get("entrez_id") or None,
                         r.get("ensembl_gene_id") or None, r.get("uniprot_ids") or None,
                         r.get("omim_id") or None, alias, prev, r.get("locus_group"),
                         r.get("status")))
            names.append((r["symbol"].upper(), hid, "symbol"))
            names += [(a.upper(), hid, "alias") for a in alias.split("|") if a]
            names += [(p.upper(), hid, "prev") for p in prev.split("|") if p]
    con.executemany("INSERT INTO hgnc VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO hgnc_name VALUES (?,?,?)", names)
    return len(rows)


def build() -> None:
    con = writer("ontology")
    _schema(con)
    d = RAW / "ontology"
    for ont, fn in OBO_FILES.items():
        print(f"  {ont}: {_load_obo(con, ont, d / fn)} terms", flush=True)
    if (d / "fma.owl").is_file():
        print(f"  fma: {_load_fma(con, d / 'fma.owl')} classes", flush=True)
    print(f"  hgnc: {_load_hgnc(con, d / 'hgnc_complete_set.txt')} genes", flush=True)
    con.execute("INSERT INTO name_fts SELECT name, curie, ont, is_label FROM name")
    con.executescript("""
    CREATE INDEX name_norm ON name(norm, ont);
    CREATE INDEX name_curie ON name(curie);
    CREATE INDEX term_ont ON term(ont);
    CREATE INDEX xref_curie ON xref(curie);
    CREATE INDEX xref_x ON xref(xref COLLATE NOCASE);
    CREATE INDEX edge_subj ON edge(subj, rel);
    CREATE INDEX edge_obj ON edge(obj, rel);
    CREATE INDEX subset_curie ON subset(curie);
    CREATE INDEX alt_id ON alt(alt_id);
    CREATE INDEX annot_curie ON annot(curie);
    CREATE INDEX hgnc_symbol ON hgnc(symbol COLLATE NOCASE);
    CREATE INDEX hgnc_entrez ON hgnc(entrez);
    CREATE INDEX hgnc_ensembl ON hgnc(ensembl);
    CREATE INDEX hgnc_name_norm ON hgnc_name(norm);
    INSERT INTO name_fts(name_fts) VALUES ('optimize');
    """)
    finish("ontology", con)
