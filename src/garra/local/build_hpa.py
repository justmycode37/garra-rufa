"""hpa.sqlite from proteinatlas.json.gz (same keys as the per-gene /<ENSG>.json records),
replacing proteinatlas.org/<ENSG>.json and the search_download.php gene / category
searches.

  gene(ensg, symbol, doc)                   full record
  enriched(field, name, category, ensg, value)
      field: tissue_category_rna / cell_type_category_rna / brain_category_rna
"""

from __future__ import annotations

import gzip
import json

from garra.local import RAW, finish, writer

FILE = RAW / "hpa" / "proteinatlas.json.gz"
# search field -> (specificity key, specific-values key)
FIELDS = {"tissue_category_rna": ("RNA tissue specificity", "RNA tissue specific nTPM"),
          "cell_type_category_rna": ("RNA single cell type specificity",
                                     "RNA single cell type specific nCPM"),
          "brain_category_rna": ("RNA brain regional specificity",
                                 "RNA brain regional specific nTPM")}


def ready() -> bool:
    return FILE.is_file()


def build() -> None:
    con = writer("hpa")
    con.executescript("""
    CREATE TABLE gene(ensg TEXT PRIMARY KEY, symbol TEXT, doc TEXT);
    CREATE TABLE enriched(field TEXT, name TEXT, category TEXT, ensg TEXT, value REAL);
    """)
    with gzip.open(FILE, "rt", encoding="utf-8") as f:
        records = json.load(f)
    rows, enr = [], []
    for r in records:
        ensg = r.get("Ensembl")
        if not ensg:
            continue
        rows.append((ensg, r.get("Gene"), json.dumps(r)))
        for field, (spec_key, vals_key) in FIELDS.items():
            cat = r.get(spec_key)
            for name, v in (r.get(vals_key) or {}).items():
                try:
                    val = float(v)
                except (TypeError, ValueError):
                    val = None
                enr.append((field, name.lower(), cat, ensg, val))
    con.executemany("INSERT OR IGNORE INTO gene VALUES (?,?,?)", rows)
    con.executemany("INSERT INTO enriched VALUES (?,?,?,?,?)", enr)
    print(f"  {len(rows)} genes, {len(enr)} enrichment rows", flush=True)
    con.executescript("""
    CREATE INDEX gene_symbol ON gene(symbol COLLATE NOCASE);
    CREATE INDEX enriched_k ON enriched(field, name, category);
    """)
    finish("hpa", con)
