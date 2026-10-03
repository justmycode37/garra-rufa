"""Load approved raw files into SQLite. Skips files that are not on disk yet."""

from __future__ import annotations

import csv
import gzip
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

from garra.paths import RAW_DIR, raw_file
from garra.sources.catalog import BULK_SOURCES

from .schema import SCHEMA

GENE_SYMBOL_KEY = "SYMBOL:{symbol}"


def normalize_alias(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def parse_disease_key(raw_id: str) -> tuple[str, str, str]:
    raw_id = raw_id.strip()
    if raw_id.startswith("ORPHA:"):
        code = raw_id.split(":", 1)[1]
        return raw_id, "ORPHA", code
    if raw_id.startswith("OMIM:"):
        code = raw_id.split(":", 1)[1]
        return raw_id, "OMIM", code
    if raw_id.startswith("MONDO:"):
        code = raw_id.split(":", 1)[1]
        return raw_id, "MONDO", code
    if raw_id.isdigit() and len(raw_id) <= 7:
        key = f"ORPHA:{raw_id}"
        return key, "ORPHA", raw_id
    return raw_id, "OTHER", raw_id


def upsert_disease(
    conn: sqlite3.Connection,
    disease_key: str,
    primary_name: str,
    *,
    aliases: list[str] | None = None,
) -> None:
    namespace, code = parse_disease_key(disease_key)[1:]
    if not primary_name:
        primary_name = disease_key
    conn.execute(
        """
        INSERT INTO disease (disease_key, primary_name, namespace, code)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(disease_key) DO UPDATE SET
          primary_name = COALESCE(NULLIF(excluded.primary_name, ''), disease.primary_name)
        """,
        (disease_key, primary_name, namespace, code),
    )
    names = {primary_name, disease_key}
    if aliases:
        names.update(aliases)
    for name in names:
        alias = normalize_alias(name)
        if not alias:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO disease_alias (alias_normalized, disease_key)
            VALUES (?, ?)
            """,
            (alias, disease_key),
        )


def upsert_gene(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    ncbi_id: str | None = None,
    hgnc_id: str | None = None,
) -> str:
    symbol = symbol.strip().upper()
    gene_key = GENE_SYMBOL_KEY.format(symbol=symbol)
    conn.execute(
        """
        INSERT INTO gene (gene_key, symbol, ncbi_id, hgnc_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(gene_key) DO UPDATE SET
          ncbi_id = COALESCE(excluded.ncbi_id, gene.ncbi_id),
          hgnc_id = COALESCE(excluded.hgnc_id, gene.hgnc_id)
        """,
        (gene_key, symbol, ncbi_id, hgnc_id),
    )
    conn.execute(
        "INSERT OR IGNORE INTO gene_alias (alias_normalized, gene_key) VALUES (?, ?)",
        (normalize_alias(symbol), gene_key),
    )
    return gene_key


def load_manifest(conn: sqlite3.Connection) -> None:
    index = RAW_DIR / "manifest.json"
    if not index.exists():
        return
    payload = json.loads(index.read_text(encoding="utf-8"))
    for source_id, row in (payload.get("sources") or {}).items():
        if row.get("status") != "ok":
            continue
        conn.execute(
            """
            INSERT INTO source_manifest (source_id, retrieved_at, publisher, license, source_url, sha256)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
              retrieved_at = excluded.retrieved_at,
              sha256 = excluded.sha256
            """,
            (
                source_id,
                row.get("retrieved_at"),
                row.get("publisher"),
                row.get("license"),
                row.get("source_url"),
                row.get("sha256"),
            ),
        )


def load_hpo_genes_to_disease(conn: sqlite3.Connection) -> dict:
    source = BULK_SOURCES["hpo_genes_to_disease"]
    path = raw_file(source.id, source.filename)
    stats = {"rows": 0, "path": str(path), "loaded": False}
    if not path.is_file():
        return stats
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            symbol = (row.get("gene_symbol") or "").strip()
            disease_id = (row.get("disease_id") or "").strip()
            if not symbol or not disease_id:
                continue
            ncbi = (row.get("ncbi_gene_id") or "").replace("NCBIGene:", "")
            gene_key = upsert_gene(conn, symbol=symbol, ncbi_id=ncbi or None)
            disease_key, _, _ = parse_disease_key(disease_id)
            upsert_disease(conn, disease_key, disease_key)
            conn.execute(
                """
                INSERT OR IGNORE INTO edge_gene_disease
                  (gene_key, disease_key, association_type, source_id, assertion)
                VALUES (?, ?, ?, ?, 'curated')
                """,
                (gene_key, disease_key, row.get("association_type") or "", source.id),
            )
            stats["rows"] += 1
    stats["loaded"] = True
    return stats


def load_monarch_causal_gene_disease(conn: sqlite3.Connection) -> dict:
    source = BULK_SOURCES["monarch_causal_gene_to_disease"]
    path = raw_file(source.id, source.filename)
    stats = {"rows": 0, "path": str(path), "loaded": False}
    if not path.is_file():
        return stats
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            symbol = (row.get("subject_label") or "").strip()
            disease_key = (row.get("object") or "").strip()
            disease_name = (row.get("object_label") or "").strip()
            subject = (row.get("subject") or "").strip()
            if not symbol or not disease_key:
                continue
            hgnc = subject.replace("HGNC:", "") if subject.startswith("HGNC:") else None
            gene_key = upsert_gene(conn, symbol=symbol, hgnc_id=hgnc)
            extra_aliases = [disease_name] if disease_name else []
            lower = (disease_name or "").lower()
            if "pompe" in lower or "acid maltase" in lower or "glycogen storage disease ii" in lower:
                extra_aliases.extend(["pompe disease", "pompe", "gsd ii", "gsd2"])
            upsert_disease(conn, disease_key, disease_name or disease_key, aliases=extra_aliases)
            conn.execute(
                """
                INSERT OR IGNORE INTO edge_gene_disease
                  (gene_key, disease_key, association_type, source_id, assertion)
                VALUES (?, ?, ?, ?, 'curated')
                """,
                (gene_key, disease_key, "causal", source.id),
            )
            stats["rows"] += 1
    stats["loaded"] = True
    return stats


def load_monarch_gene_pathway(conn: sqlite3.Connection) -> dict:
    source = BULK_SOURCES["monarch_gene_to_pathway"]
    path = raw_file(source.id, source.filename)
    stats = {"rows": 0, "path": str(path), "loaded": False}
    if not path.is_file():
        return stats
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            symbol = (row.get("subject_label") or "").strip()
            pathway_key = (row.get("object") or "").strip()
            pathway_label = (row.get("object_label") or "").strip()
            if not symbol or not pathway_key:
                continue
            gene_key = upsert_gene(conn, symbol=symbol)
            conn.execute(
                """
                INSERT INTO pathway (pathway_key, label) VALUES (?, ?)
                ON CONFLICT(pathway_key) DO UPDATE SET label = excluded.label
                """,
                (pathway_key, pathway_label or pathway_key),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO edge_gene_pathway (gene_key, pathway_key, source_id)
                VALUES (?, ?, ?)
                """,
                (gene_key, pathway_key, source.id),
            )
            stats["rows"] += 1
    stats["loaded"] = True
    return stats


def load_hpo_disease_phenotype(conn: sqlite3.Connection) -> dict:
    source = BULK_SOURCES["hpo_disease_phenotype"]
    path = raw_file(source.id, source.filename)
    stats = {"rows": 0, "path": str(path), "loaded": False}
    if not path.is_file():
        return stats
    with path.open(encoding="utf-8") as handle:
        header_seen = False
        for line in handle:
            if line.startswith("#"):
                continue
            if not header_seen:
                if not line.startswith("database_id"):
                    continue
                header_seen = True
                headers = line.rstrip("\n").split("\t")
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < len(headers):
                continue
            row = dict(zip(headers, parts, strict=False))
            disease_id = (row.get("database_id") or "").strip()
            hpo_id = (row.get("hpo_id") or "").strip()
            if not disease_id or not hpo_id:
                continue
            disease_key, _, _ = parse_disease_key(disease_id)
            label = (row.get("hpo_name") or "").strip()
            conn.execute(
                "INSERT INTO phenotype (hpo_id, label) VALUES (?, ?) ON CONFLICT(hpo_id) DO UPDATE SET label = excluded.label",
                (hpo_id, label or hpo_id),
            )
            upsert_disease(conn, disease_key, disease_key)
            conn.execute(
                """
                INSERT OR IGNORE INTO edge_disease_phenotype
                  (disease_key, hpo_id, frequency, source_id)
                VALUES (?, ?, ?, ?)
                """,
                (disease_key, hpo_id, row.get("frequency") or "", source.id),
            )
            stats["rows"] += 1
    stats["loaded"] = True
    return stats


def _text(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def load_orphadata_diseases(conn: sqlite3.Connection) -> dict:
    source = BULK_SOURCES["orphadata_diseases"]
    path = raw_file(source.id, source.filename)
    stats = {"rows": 0, "path": str(path), "loaded": False}
    if not path.is_file():
        return stats
    root = ET.parse(path).getroot()
    for disorder in root.findall(".//Disorder"):
        orpha = (disorder.findtext("OrphaCode") or "").strip()
        if not orpha:
            continue
        disease_key = f"ORPHA:{orpha}"
        name = _text(disorder.find("Name"))
        aliases = []
        for syn in disorder.findall(".//Synonym"):
            text = _text(syn)
            if text:
                aliases.append(text)
        upsert_disease(conn, disease_key, name or disease_key, aliases=aliases)
        stats["rows"] += 1
    stats["loaded"] = True
    return stats


def load_orphadata_genes(conn: sqlite3.Connection) -> dict:
    source = BULK_SOURCES["orphadata_genes"]
    path = raw_file(source.id, source.filename)
    stats = {"rows": 0, "path": str(path), "loaded": False}
    if not path.is_file():
        return stats
    root = ET.parse(path).getroot()
    for disorder in root.findall(".//Disorder"):
        orpha = (disorder.findtext("OrphaCode") or "").strip()
        if not orpha:
            continue
        disease_key = f"ORPHA:{orpha}"
        name = _text(disorder.find("Name"))
        upsert_disease(conn, disease_key, name or disease_key)
        for assoc in disorder.findall(".//DisorderGeneAssociation"):
            gene = assoc.find("Gene")
            if gene is None:
                continue
            symbol = (gene.findtext("Symbol") or "").strip()
            if not symbol:
                continue
            gene_key = upsert_gene(conn, symbol=symbol)
            conn.execute(
                """
                INSERT OR IGNORE INTO edge_gene_disease
                  (gene_key, disease_key, association_type, source_id, assertion)
                VALUES (?, ?, ?, ?, 'curated')
                """,
                (gene_key, disease_key, "orphanet", source.id),
            )
            stats["rows"] += 1
    stats["loaded"] = True
    return stats


def load_orphadata_phenotypes(conn: sqlite3.Connection) -> dict:
    source = BULK_SOURCES["orphadata_phenotypes"]
    path = raw_file(source.id, source.filename)
    stats = {"rows": 0, "path": str(path), "loaded": False}
    if not path.is_file():
        return stats
    root = ET.parse(path).getroot()
    for disorder in root.findall(".//Disorder"):
        orpha = (disorder.findtext("OrphaCode") or "").strip()
        if not orpha:
            continue
        disease_key = f"ORPHA:{orpha}"
        name = _text(disorder.find("Name"))
        upsert_disease(conn, disease_key, name or disease_key)
        for assoc in disorder.findall(".//HPODisorderAssociation"):
            hpo = assoc.find("HPO")
            if hpo is None:
                continue
            hpo_id = (hpo.findtext("HPOId") or "").strip()
            hpo_name = _text(hpo.find("HPOTerm"))
            if not hpo_id:
                continue
            conn.execute(
                "INSERT INTO phenotype (hpo_id, label) VALUES (?, ?) ON CONFLICT(hpo_id) DO UPDATE SET label = excluded.label",
                (hpo_id, hpo_name or hpo_id),
            )
            freq = _text(assoc.find("HPOFrequency"))
            conn.execute(
                """
                INSERT OR IGNORE INTO edge_disease_phenotype
                  (disease_key, hpo_id, frequency, source_id)
                VALUES (?, ?, ?, ?)
                """,
                (disease_key, hpo_id, freq, source.id),
            )
            stats["rows"] += 1
    stats["loaded"] = True
    return stats


def seed_gene_aliases(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT gene_key, symbol FROM gene"):
        conn.execute(
            "INSERT OR IGNORE INTO gene_alias (alias_normalized, gene_key) VALUES (?, ?)",
            (normalize_alias(row["symbol"]), row["gene_key"]),
        )


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
