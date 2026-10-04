"""gtex.sqlite from the GTEx v10 bulk files, replacing the GTEx Portal API v2 endpoints
sources/gtex.py calls.

  gene(gencode, symbol, entrez)               genes of the median-TPM table
  median(gencode, tissue, median)             median TPM per tissue (> 0 only)
  qtl(kind, gencode, variant, pvalue, tissue) e / s QTLs: per gene the TOP_PER_GENE
                                              variants with the best p-value over tissues
  egene(kind, tissue, gencode, qvalue)        eGenes / sGenes per tissue (q < 0.05)
  rsid(variant, rs)                           dbSNP ids of the kept variants
  meta(key, value)                            tissueSiteDetail JSON
"""

from __future__ import annotations

import csv
import gzip
import io
import sqlite3
import sys
import tarfile

from garra.local import RAW, db_path, finish, writer

DIR = RAW / "gtex"
MEDIAN = DIR / "GTEx_Analysis_v10_RNASeQCv2.4.2_gene_median_tpm.gct.gz"
TARS = {"e": DIR / "GTEx_Analysis_v10_eQTL.tar", "s": DIR / "GTEx_Analysis_v10_sQTL.tar"}
LOOKUP = DIR / "GTEx_Analysis_2021-02-11_v10_WholeGenomeSeq_953Indiv.lookup_table.txt.gz"
TOP_PER_GENE = 200
EGENE_Q = 0.05


def ready() -> bool:
    return MEDIAN.is_file() and all(p.is_file() for p in TARS.values()) \
        and (DIR / "tissueSiteDetail.json").is_file()


def _qtls(kind: str):
    """(gene, variant, pvalue, tissue) best per gene/variant, top TOP_PER_GENE per gene."""
    import pandas as pd
    import pyarrow.parquet as pq
    parts = []
    egenes = []
    with tarfile.open(TARS[kind]) as t:
        members = [m for m in t.getmembers() if m.isfile()]
        for i, m in enumerate(members):
            tissue = m.name.rsplit("/", 1)[-1].split(".v10.")[0]
            data = t.extractfile(m).read()
            if m.name.endswith(".parquet"):
                df = pq.read_table(io.BytesIO(data), columns=["gene_id", "variant_id",
                                                              "pval_nominal"]).to_pandas()
                df["tissue"] = tissue
                df = df.sort_values("pval_nominal").drop_duplicates(["gene_id", "variant_id"])
                parts.append(df.groupby("gene_id").head(TOP_PER_GENE))
            elif m.name.endswith(".txt.gz"):
                rows = csv.DictReader(io.StringIO(gzip.decompress(data).decode()),
                                      delimiter="\t")
                for r in rows:
                    try:
                        q = float(r["qval"])
                    except (KeyError, ValueError):
                        continue
                    if q < EGENE_Q:
                        egenes.append((kind, tissue, r["gene_id"], q))
            if i % 20 == 0:
                print(f"  ... {kind}QTL {i + 1}/{len(members)}", file=sys.stderr, flush=True)
    df = pd.concat(parts).sort_values("pval_nominal")
    df = df.drop_duplicates(["gene_id", "variant_id"]).groupby("gene_id").head(TOP_PER_GENE)
    return df, egenes


def build(keep_raw: bool = True) -> None:
    con = writer("gtex")
    con.executescript("""
    CREATE TABLE gene(gencode TEXT PRIMARY KEY, symbol TEXT, entrez TEXT);
    CREATE TABLE median(gencode TEXT, tissue TEXT, median REAL);
    CREATE TABLE qtl(kind TEXT, gencode TEXT, variant TEXT, pvalue REAL, tissue TEXT);
    CREATE TABLE egene(kind TEXT, tissue TEXT, gencode TEXT, qvalue REAL);
    CREATE TABLE rsid(variant TEXT PRIMARY KEY, rs TEXT);
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    """)
    con.execute("INSERT INTO meta VALUES ('tissueSiteDetail', ?)",
                ((DIR / "tissueSiteDetail.json").read_text(encoding="utf-8"),))
    entrez = {}
    if db_path("ontology").is_file():
        o = sqlite3.connect(db_path("ontology"))
        entrez = {e: x for e, x in o.execute("SELECT ensembl, entrez FROM hgnc "
                                             "WHERE ensembl IS NOT NULL")}
        o.close()
    genes, med = [], []
    with gzip.open(MEDIAN, "rt", encoding="utf-8") as f:
        f.readline(), f.readline()
        r = csv.reader(f, delimiter="\t")
        head = next(r)
        tissues = head[2:]
        for row in r:
            gid, sym = row[0], row[1]
            genes.append((gid, sym, entrez.get(gid.split(".")[0])))
            for t, v in zip(tissues, row[2:]):
                if float(v) > 0:
                    med.append((gid, t, float(v)))
    con.executemany("INSERT INTO gene VALUES (?,?,?)", genes)
    con.executemany("INSERT INTO median VALUES (?,?,?)", med)
    print(f"  {len(genes)} genes, {len(med)} median values", flush=True)
    kept: set[str] = set()
    for kind in ("e", "s"):
        df, eg = _qtls(kind)
        con.executemany("INSERT INTO qtl VALUES (?,?,?,?,?)",
                        ((kind, g, v, float(p), t) for g, v, p, t in
                         df[["gene_id", "variant_id", "pval_nominal", "tissue"]]
                         .itertuples(index=False, name=None)))
        con.executemany("INSERT INTO egene VALUES (?,?,?,?)", eg)
        kept.update(df["variant_id"].tolist())
        print(f"  {kind}QTL: {len(df)} gene-variant pairs, {len(eg)} {kind}Genes", flush=True)
    rows = []
    with gzip.open(LOOKUP, "rt", encoding="utf-8") as f:
        f.readline()
        for line in f:
            vid, rest = line.split("\t", 1)
            if vid in kept:
                rs = line.rstrip("\n").split("\t")[6]
                if rs.startswith("rs"):
                    rows.append((vid, rs))
    con.executemany("INSERT OR IGNORE INTO rsid VALUES (?,?)", rows)
    print(f"  {len(rows)} rs ids", flush=True)
    con.executescript("""
    CREATE INDEX gene_symbol ON gene(symbol COLLATE NOCASE);
    CREATE INDEX median_g ON median(gencode);
    CREATE INDEX median_t ON median(tissue, median);
    CREATE INDEX qtl_g ON qtl(kind, gencode, pvalue);
    CREATE INDEX qtl_v ON qtl(kind, variant);
    CREATE INDEX egene_t ON egene(kind, tissue, qvalue);
    CREATE INDEX rsid_rs ON rsid(rs);
    """)
    finish("gtex", con)
    if not keep_raw:
        for p in (*TARS.values(), LOOKUP):
            p.unlink(missing_ok=True)
