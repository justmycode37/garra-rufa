"""hpo.sqlite from the HPO annotation files, replacing the JAX HPO API "network" endpoints
(ontology terms themselves come from ontology.sqlite, which must be built first).

  disease(id, name, mondo)                          OMIM / ORPHA / DECIPHER diseases
  dpheno(disease, hp, freq, onset, sex, refs, category)   phenotype.hpoa, aspect P, not NOT
  gpheno(gene, hp)                                  genes_to_phenotype (distinct)
  gdisease(gene, symbol, disease, assoc)            genes_to_disease
  gene(gene, symbol)                                NCBIGene:<id> -> symbol
"""

from __future__ import annotations

import csv
import sqlite3

from garra.local import RAW, db_path, finish, writer

DIR = RAW / "hpo"
PHENOTYPE_ROOT = "HP:0000118"


def ready() -> bool:
    return all((DIR / f).is_file() for f in ("phenotype.hpoa", "genes_to_phenotype.txt",
                                             "genes_to_disease.txt")) \
        and db_path("ontology").is_file()


def _rows(path):
    with open(path, encoding="utf-8") as f:
        yield from csv.DictReader((x for x in f if not x.startswith("#")), delimiter="\t")


def _categories(ont: sqlite3.Connection) -> dict[str, str]:
    """HP term -> label of the top-level system (child of Phenotypic abnormality) it is
    under; the first one found when a term sits under several."""
    tops = {c: lab for c, lab in ont.execute(
        "SELECT e.subj, t.label FROM edge e JOIN term t ON t.curie=e.subj "
        "WHERE e.obj=? AND e.rel='is_a'", (PHENOTYPE_ROOT,))}
    parents: dict[str, list[str]] = {}
    for s, o in ont.execute("SELECT subj, obj FROM edge WHERE rel='is_a' AND subj LIKE 'HP:%'"):
        parents.setdefault(s, []).append(o)
    memo: dict[str, str | None] = {}

    def cat(hp: str, depth: int = 0) -> str | None:
        if hp in tops:
            return tops[hp]
        if hp in memo or depth > 40:
            return memo.get(hp)
        memo[hp] = None
        for p in parents.get(hp, []):
            c = cat(p, depth + 1)
            if c:
                memo[hp] = c
                break
        return memo[hp]

    return {hp: c for hp in parents if (c := cat(hp))}


def build(keep_raw: bool = True) -> None:
    ont = sqlite3.connect(db_path("ontology"))
    cats = _categories(ont)
    alt = dict(ont.execute("SELECT alt_id, curie FROM alt WHERE alt_id LIKE 'HP:%'"))
    mondo: dict[str, str] = {}
    for c, x in ont.execute("SELECT x.curie, x.xref FROM xref x JOIN term t ON t.curie=x.curie "
                            "WHERE t.ont='mondo' AND t.obsolete=0 AND (x.xref LIKE 'OMIM:%' "
                            "OR x.xref LIKE 'Orphanet:%' OR x.xref LIKE 'DECIPHER:%')"):
        mondo.setdefault(x.replace("Orphanet:", "ORPHA:"), c)
    ont.close()

    con = writer("hpo")
    con.executescript("""
    CREATE TABLE disease(id TEXT PRIMARY KEY, name TEXT, mondo TEXT);
    CREATE TABLE dpheno(disease TEXT, hp TEXT, freq TEXT, onset TEXT, sex TEXT, refs TEXT,
                        category TEXT, aspect TEXT);
    CREATE TABLE gpheno(gene TEXT, hp TEXT);
    CREATE TABLE gdisease(gene TEXT, symbol TEXT, disease TEXT, assoc TEXT);
    CREATE TABLE gene(gene TEXT PRIMARY KEY, symbol TEXT);
    """)
    names: dict[str, str] = {}
    rows = []
    for r in _rows(DIR / "phenotype.hpoa"):
        d = r["database_id"].replace("ORPHANET:", "ORPHA:")
        names.setdefault(d, r["disease_name"])
        if r.get("qualifier") == "NOT":
            continue
        hp = alt.get(r["hpo_id"], r["hpo_id"])
        rows.append((d, hp, r.get("frequency") or None, r.get("onset") or None,
                     r.get("sex") or None, r.get("reference") or None,
                     cats.get(hp, "Other") if r.get("aspect") == "P" else None, r.get("aspect")))
    con.executemany("INSERT INTO dpheno VALUES (?,?,?,?,?,?,?,?)", rows)
    genes: dict[str, str] = {}
    gp = set()
    for r in _rows(DIR / "genes_to_phenotype.txt"):
        g = f"NCBIGene:{r['ncbi_gene_id']}"
        genes[g] = r["gene_symbol"]
        gp.add((g, alt.get(r["hpo_id"], r["hpo_id"])))
    con.executemany("INSERT INTO gpheno VALUES (?,?)", sorted(gp))
    gd = []
    for r in _rows(DIR / "genes_to_disease.txt"):
        g = r["ncbi_gene_id"] if r["ncbi_gene_id"].startswith("NCBIGene:") \
            else f"NCBIGene:{r['ncbi_gene_id']}"
        genes[g] = r["gene_symbol"]
        d = r["disease_id"].replace("ORPHANET:", "ORPHA:")
        gd.append((g, r["gene_symbol"], d, r.get("association_type")))
    con.executemany("INSERT INTO gdisease VALUES (?,?,?,?)", gd)
    con.executemany("INSERT INTO gene VALUES (?,?)", sorted(genes.items()))
    con.executemany("INSERT INTO disease VALUES (?,?,?)",
                    [(d, n, mondo.get(d)) for d, n in names.items()])
    print(f"  {len(names)} diseases, {len(rows)} annotations, {len(gp)} gene-phenotype, "
          f"{len(gd)} gene-disease", flush=True)
    con.executescript("""
    CREATE VIRTUAL TABLE disease_fts USING fts5(name, id UNINDEXED);
    INSERT INTO disease_fts SELECT name, id FROM disease;
    CREATE INDEX dpheno_d ON dpheno(disease);
    CREATE INDEX dpheno_hp ON dpheno(hp);
    CREATE INDEX gpheno_g ON gpheno(gene);
    CREATE INDEX gpheno_hp ON gpheno(hp);
    CREATE INDEX gdisease_g ON gdisease(gene);
    CREATE INDEX gdisease_d ON gdisease(disease);
    CREATE INDEX gene_symbol ON gene(symbol COLLATE NOCASE);
    CREATE INDEX disease_mondo ON disease(mondo);
    """)
    finish("hpo", con)
