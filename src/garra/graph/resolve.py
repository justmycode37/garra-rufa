"""Resolve a user query to diseases and/or genes."""

from __future__ import annotations

import re
import sqlite3

from garra.ingest.loaders import normalize_alias
from garra.paths import ATLAS_DB

from .store import AtlasStore


def resolve_query(query: str, *, db_path=None) -> dict:
    text = query.strip()
    if not text:
        return {"status": "rejected", "message": "query is empty", "matches": []}

    store = AtlasStore(db_path or ATLAS_DB)
    normalized = normalize_alias(text)
    matches: list[dict] = []

    with store.connect() as conn:
        matches.extend(_match_disease_aliases(conn, normalized, text))
        matches.extend(_match_gene_aliases(conn, normalized, text))
        if not matches:
            matches.extend(_fuzzy_disease(conn, normalized))
        if not matches and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{1,15}", text):
            matches.extend(_match_gene_symbol(conn, text.upper()))

    if not matches:
        return {
            "status": "not_found",
            "query": text,
            "message": "No disease or gene matched. Try a full disease name, Orpha/OMIM id, or gene symbol.",
            "matches": [],
        }

    identities = {(m["type"], m.get("disease_key") or m.get("gene_key")) for m in matches}
    if len(identities) > 1:
        return {
            "status": "ambiguous",
            "query": text,
            "matches": matches,
            "message": "Multiple entities match. Choose a stable disease or gene identifier before continuing.",
        }
    primary = _pick_primary(matches)
    return {
        "status": "ok",
        "query": text,
        "matches": matches,
        "primary": primary,
    }


def _match_disease_aliases(conn: sqlite3.Connection, normalized: str, raw: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT a.disease_key, d.primary_name, d.namespace
        FROM disease_alias a
        JOIN disease d ON d.disease_key = a.disease_key
        WHERE a.alias_normalized = ?
        ORDER BY d.primary_name
        LIMIT 20
        """,
        (normalized,),
    ).fetchall()
    return [
        {
            "type": "disease",
            "disease_key": row["disease_key"],
            "name": row["primary_name"] or row["disease_key"],
            "namespace": row["namespace"],
            "match": raw,
        }
        for row in rows
    ]


def _match_gene_aliases(conn: sqlite3.Connection, normalized: str, raw: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT g.gene_key, g.symbol, g.ncbi_id, g.hgnc_id
        FROM gene_alias a
        JOIN gene g ON g.gene_key = a.gene_key
        WHERE a.alias_normalized = ?
        LIMIT 5
        """,
        (normalized,),
    ).fetchall()
    return [
        {
            "type": "gene",
            "gene_key": row["gene_key"],
            "symbol": row["symbol"],
            "ncbi_id": row["ncbi_id"],
            "hgnc_id": row["hgnc_id"],
            "match": raw,
        }
        for row in rows
    ]


def _fuzzy_disease(conn: sqlite3.Connection, normalized: str) -> list[dict]:
    like = f"%{normalized}%"
    rows = conn.execute(
        """
        SELECT DISTINCT d.disease_key, d.primary_name, d.namespace
        FROM disease d
        LEFT JOIN disease_alias a ON a.disease_key = d.disease_key
        WHERE lower(d.primary_name) LIKE ? OR a.alias_normalized LIKE ?
        ORDER BY length(d.primary_name)
        LIMIT 10
        """,
        (like, like),
    ).fetchall()
    return [
        {
            "type": "disease",
            "disease_key": row["disease_key"],
            "name": row["primary_name"],
            "namespace": row["namespace"],
            "match": "name_substring",
        }
        for row in rows
    ]


def _match_gene_symbol(conn: sqlite3.Connection, symbol: str) -> list[dict]:
    row = conn.execute(
        "SELECT gene_key, symbol, ncbi_id, hgnc_id FROM gene WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    if not row:
        return []
    return [
        {
            "type": "gene",
            "gene_key": row["gene_key"],
            "symbol": row["symbol"],
            "ncbi_id": row["ncbi_id"],
            "hgnc_id": row["hgnc_id"],
            "match": symbol,
        }
    ]


def _pick_primary(matches: list[dict]) -> dict:
    diseases = [m for m in matches if m["type"] == "disease"]
    genes = [m for m in matches if m["type"] == "gene"]
    if diseases:
        return {"kind": "disease", **diseases[0]}
    if genes:
        return {"kind": "gene", **genes[0]}
    return {"kind": "unknown"}
