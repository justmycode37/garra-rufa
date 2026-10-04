"""clinvar.sqlite from ClinVar variant_summary + var_citations, replacing E-utilities
esearch / esummary / elink for db=clinvar.

  variant(vid, name, rs, germline, review, nsub, oncogenicity, clinical_impact, traits,
          clingen, pathogenic)                  one row per VariationID (GRCh38 preferred)
  vgene(vid, geneid, symbol, hgnc)
  vtrait(vid, prefix, local)                    MONDO / ORPHA / OMIM / HP / UMLS ids
  cite(vid, pmid)
"""

from __future__ import annotations

import csv
import gzip
import json

from garra.local import RAW, finish, writer

DIR = RAW / "clinvar"
# variant_summary xref prefix -> (esummary db_source, trait index prefix)
SOURCES = {"MONDO": ("MONDO", "MONDO"), "Orphanet": ("Orphanet", "ORPHA"),
           "OMIM": ("OMIM", "OMIM"), "HP": ("Human Phenotype Ontology", "HP"),
           "Human Phenotype Ontology": ("Human Phenotype Ontology", "HP"),
           "MedGen": ("MedGen", "UMLS")}
REVIEW_RANK = {"practice guideline": 4, "reviewed by expert panel": 3,
               "criteria provided, multiple submitters, no conflicts": 2,
               "criteria provided, conflicting classifications": 1,
               "criteria provided, single submitter": 1}


def ready() -> bool:
    return (DIR / "variant_summary.txt.gz").is_file()


def _traits(ids: str, names: str) -> list[dict]:
    """PhenotypeIDS / PhenotypeList -> [{trait_name, trait_xrefs}], deduplicated."""
    out, seen = [], set()
    id_groups = [g for part in ids.split("|") for g in part.split(";")]
    name_groups = [n for part in names.split("|") for n in part.split(";")]
    for i, g in enumerate(id_groups):
        name = name_groups[i] if i < len(name_groups) else ""
        xrefs = []
        for x in g.split(","):
            src, _, local = x.partition(":")
            if src in SOURCES and local:
                xrefs.append({"db_source": SOURCES[src][0], "db_id": local})
        key = (name, tuple((x["db_source"], x["db_id"]) for x in xrefs))
        if key in seen or (not xrefs and name in ("not provided", "not specified", "-", "")):
            continue
        seen.add(key)
        out.append({"trait_name": name, "trait_xrefs": xrefs})
    return out


def _index_key(xref: dict) -> tuple[str, str] | None:
    for src, (db_source, prefix) in SOURCES.items():
        if xref["db_source"] == db_source:
            local = xref["db_id"].split(":", 1)[-1]
            return prefix, local.upper()
    return None


def build() -> None:
    con = writer("clinvar")
    con.executescript("""
    CREATE TABLE variant(vid INTEGER PRIMARY KEY, name TEXT, rs TEXT, germline TEXT,
                         review TEXT, rank INTEGER, nsub INTEGER, oncogenicity TEXT,
                         clinical_impact TEXT, traits TEXT, clingen TEXT, pathogenic INTEGER);
    CREATE TABLE vgene(vid INTEGER, geneid TEXT, symbol TEXT, hgnc TEXT);
    CREATE TABLE vtrait(vid INTEGER, prefix TEXT, local TEXT);
    CREATE TABLE cite(vid INTEGER, pmid TEXT);
    """)
    best: dict[int, str] = {}  # vid -> assembly kept
    rows, genes, traits = {}, {}, {}
    with gzip.open(DIR / "variant_summary.txt.gz", "rt", encoding="utf-8") as f:
        r = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        h = {k.lstrip("#"): i for i, k in enumerate(next(r))}
        for x in r:
            vid = int(x[h["VariationID"]])
            asm = x[h["Assembly"]]
            if vid in best and not (asm == "GRCh38" and best[vid] != "GRCh38"):
                continue
            best[vid] = asm
            tr = _traits(x[h["PhenotypeIDS"]], x[h["PhenotypeList"]])
            sig = x[h["ClinicalSignificance"]]
            rs = x[h["RS# (dbSNP)"]]
            clingen = next((o.split(":", 1)[1] for o in x[h["OtherIDs"]].split(",")
                            if o.startswith("ClinGen:")), None)
            review = x[h["ReviewStatus"]]
            rows[vid] = (vid, x[h["Name"]], rs if rs not in ("-1", "-", "") else None, sig,
                         review, REVIEW_RANK.get(review, 0),
                         int(x[h["NumberSubmitters"]] or 0),
                         None if x[h["Oncogenicity"]] in ("-", "") else x[h["Oncogenicity"]],
                         None if x[h["SomaticClinicalImpact"]] in ("-", "")
                         else x[h["SomaticClinicalImpact"]],
                         json.dumps(tr), clingen,
                         int("pathogenic" in sig.lower() and "conflicting" not in sig.lower()))
            gids = [g for g in x[h["GeneID"]].split(";") if g and g != "-1"]
            syms = [s for s in x[h["GeneSymbol"]].split(";") if s and s != "-"]
            hg = x[h["HGNC_ID"]] if x[h["HGNC_ID"]] not in ("-", "") else None
            genes[vid] = [(vid, g, syms[i] if i < len(syms) else None, hg if i == 0 else None)
                          for i, g in enumerate(gids)] or \
                [(vid, None, s, hg if i == 0 else None) for i, s in enumerate(syms)]
            keys = {k for t in tr for k in map(_index_key, t["trait_xrefs"]) if k}
            traits[vid] = [(vid, p, loc) for p, loc in keys]
    con.executemany("INSERT INTO variant VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows.values())
    con.executemany("INSERT INTO vgene VALUES (?,?,?,?)", (g for gs in genes.values() for g in gs))
    con.executemany("INSERT INTO vtrait VALUES (?,?,?)", (t for ts in traits.values() for t in ts))
    print(f"  {len(rows)} variants", flush=True)
    cites = DIR / "var_citations.txt"
    if cites.is_file():
        seen = set()
        with open(cites, encoding="utf-8") as f:
            r = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
            h = {k.lstrip("#"): i for i, k in enumerate(next(r))}
            for x in r:
                if x[h["citation_source"]] in ("PubMed", "PubMedCentral") and \
                        x[h["citation_source"]] == "PubMed":
                    seen.add((int(x[h["VariationID"]]), x[h["citation_id"]]))
        con.executemany("INSERT INTO cite VALUES (?,?)", sorted(seen))
        print(f"  {len(seen)} citations", flush=True)
    con.executescript("""
    CREATE INDEX vgene_g ON vgene(geneid);
    CREATE INDEX vgene_s ON vgene(symbol COLLATE NOCASE);
    CREATE INDEX vgene_h ON vgene(hgnc);
    CREATE INDEX vgene_v ON vgene(vid);
    CREATE INDEX vtrait_k ON vtrait(prefix, local);
    CREATE INDEX variant_rs ON variant(rs);
    CREATE INDEX cite_v ON cite(vid);
    """)
    finish("clinvar", con)
