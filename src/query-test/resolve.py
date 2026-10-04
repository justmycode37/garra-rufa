"""Read a free-form input: is it a disease, or symptoms / genes that point to diseases?

  interpret("Marfan syndrome")                        -> disease (name search, as before)
  interpret("FBN1")                                   -> gene -> its diseases
  interpret("tall stature, arachnodactyly, HP:0001083") -> phenotypes -> ranked diseases
  interpret("PMM2; cerebellar hypoplasia; inverted nipples")  -> both

The whole input is tried first ("Ectopia lentis, familial" is one disease name), then the
parts between ",", ";", newlines and " + "; a part that is not recognised is split further
at " and " / " with " only when every piece is recognised ("seizures and loss of speech",
but not "burning pain in hands and feet"). Each part is read as:

  CURIE       HP: -> phenotype; NCBIGene:/HGNC:/ENSEMBL:/SYMBOL: -> gene; else disease
  gene        an HGNC symbol (or unique alias / previous symbol) of a gene that has disease
              annotations in HPO; written in upper case or containing a digit, so words
              such as "cat" or "light" are not read as genes
  disease     an exact Orphanet / HPO (OMIM, ORPHA) / MONDO disease name or synonym
              (acronyms such as "CAT" only in the same case)
  phenotype   an HPO term: exact label or synonym, else (parts of two or more words that
              contain no disease word) every word of the part in one HPO label
              ("approximate")

A name that is both an HPO phenotype and a disease ("alopecia", "ectopia lentis",
"amyotrophic lateral sclerosis") is read with Orphanet's type of the entry: diseases,
malformation syndromes and subtypes stay diseases; categories, clinical groups,
morphological anomalies and clinical syndromes are read as the phenotype. The other
reading is kept in Part.alternative and shown in the reports.

The result's mode is "disease" (one disease part, or an unrecognised single name: the
name search of main.py, unchanged), "candidates" (any phenotype / gene part: diseases
are ranked by _hpoa.rank_diseases and the top ones expanded; disease parts of a mixed
input are pinned at the top) or "none" (several parts, none recognised).

symptom_anchor (improvements.py): with --symptoms (prefer_phenotype) an item that is a
disease name but also an HPO term in looser spelling ("cone-rod dystrophy") is read as the
phenotype, an unrecognised item whose words all start the words of a suggested HPO term
("stereotypic hand wringing") as that term; rank() merges entries of one disease
(dedupe_ranking: shared OMIM / ORPHA / MONDO ids or folded name) and focus_gate() picks
the candidates close enough to the top for a full profile.

Uses the local indexes in data/local/ (ontology.sqlite: HGNC + MONDO names,
orphadata.sqlite: Orphanet names and types) when present; without them only HPO's own
gene and disease names are known.
"""
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import improvements
from sources import _hpoa

LOCAL = Path(__file__).resolve().parents[2] / "data" / "local"
CURIE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*:\S+$")
SPLIT = re.compile(r"\s*(?:[;,\n]|\s\+\s)\s*")
SPLIT_AND = re.compile(r"\s+(?:and|with|plus|&)\s+", re.I)
GENE_PREFIXES = {"NCBIGENE", "ENTREZ", "HGNC", "ENSEMBL", "SYMBOL"}
# Orphanet entry types that are read as the disease when the name is also an HPO term
ORPHA_DISEASE_TYPES = {"Disease", "Malformation syndrome", "Clinical subtype",
                       "Histopathological subtype", "Etiological subtype"}
# annotation_coverage: exponent of the coverage factor (_hpoa.HpoData.rank_diseases);
# tuned on phenopacket-store patients (phenobench.py rank, leave-publication-out)
COVERAGE = 0.5
DISEASE_WORDS = {"syndrome", "disease", "disorder", "deficiency", "dystrophy", "cdg",
                 "anemia", "ataxia", "type", "familial", "hereditary", "congenital"}


@dataclass
class Part:
    text: str
    kind: str  # disease | gene | phenotype | unknown
    id: str | None = None  # HP id, gene symbol, or disease CURIE (OMIM/ORPHA preferred)
    label: str | None = None
    how: str = ""  # id | symbol | alias | exact | synonym | approximate | name
    alternative: str | None = None  # the other reading of an ambiguous name
    suggestions: list[tuple[str, str]] = field(default_factory=list)  # (HP id, name)

    def as_dict(self) -> dict:
        return {k: v for k, v in vars(self).items() if v not in (None, "", [])}


@dataclass
class Interpretation:
    text: str
    parts: list[Part]

    @property
    def mode(self) -> str:
        if any(p.kind in ("gene", "phenotype") for p in self.parts):
            return "candidates"
        if len(self.parts) > 1 and not self.of("disease"):
            return "none"
        return "disease"

    def of(self, kind: str) -> list[Part]:
        return [p for p in self.parts if p.kind == kind]


# -- local indexes (optional) ---------------------------------------------------------
@lru_cache(maxsize=None)
def _db(name: str) -> sqlite3.Connection | None:
    path = LOCAL / f"{name}.sqlite"
    if not path.exists():
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    except sqlite3.Error:
        return None


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _orpha_names(text: str) -> list[tuple[str, str, str]]:
    """Orphanet entries named `text` exactly: (ORPHA:code, preferred name, type). An
    acronym ("CAT", "SLE") matches only when written the same way ("cat" does not)."""
    db = _db("orphadata")
    if db is None:
        return []
    try:
        rows = db.execute("SELECT DISTINCT d.code, d.name, d.type, n.name FROM oname n JOIN "
                          "disorder d ON d.code = n.code WHERE n.norm = ?",
                          (_norm(text),)).fetchall()
    except sqlite3.Error:
        return []
    t = " ".join(text.split())
    return [(f"ORPHA:{c}", n, typ) for c, n, typ, matched in rows
            if not (matched.isupper() and matched != t)]


def _mondo_names(text: str) -> list[tuple[str, str]]:
    db = _db("ontology")
    if db is None:
        return []
    try:
        rows = db.execute("SELECT DISTINCT n.curie, t.label FROM name n JOIN term t ON "
                          "t.curie = n.curie WHERE n.ont = 'mondo' AND n.norm = ? AND "
                          "n.scope IN ('LABEL', 'EXACT') AND NOT t.obsolete",
                          (_norm(text),)).fetchall()
    except sqlite3.Error:
        return []
    return rows


def _mondo_to_hpo_ids(mondo: str) -> list[str]:
    """OMIM / ORPHA ids of a MONDO disease (exact xrefs), as HPO annotates them."""
    db = _db("ontology")
    if db is None:
        return []
    try:
        rows = db.execute("SELECT xref FROM xref WHERE curie = ?", (mondo,)).fetchall()
    except sqlite3.Error:
        return []
    out = []
    for (x,) in rows:
        p, _, i = x.partition(":")
        p = {"ORPHANET": "ORPHA", "MIM": "OMIM"}.get(p.upper(), p.upper())
        if p in ("OMIM", "ORPHA"):
            out.append(f"{p}:{i}")
    return out


def _hgnc(text: str) -> list[tuple[str, str, str]]:
    """HGNC genes whose symbol / alias / previous symbol is `text`: (symbol, entrez, kind)."""
    db = _db("ontology")
    if db is None:
        return []
    try:
        return db.execute("SELECT DISTINCT h.symbol, h.entrez, n.kind FROM hgnc_name n "
                          "JOIN hgnc h ON h.hgnc_id = n.hgnc_id WHERE n.norm = ?",
                          (text.strip().upper(),)).fetchall()
    except sqlite3.Error:
        return []


def _hgnc_by_id(curie: str) -> str | None:
    db = _db("ontology")
    if db is None:
        return None
    p, _, i = curie.partition(":")
    col = {"HGNC": "hgnc_id", "ENSEMBL": "ensembl"}.get(p.upper())
    if not col:
        return None
    val = f"HGNC:{i}" if col == "hgnc_id" else i
    try:
        row = db.execute(f"SELECT symbol FROM hgnc WHERE {col} = ?", (val,)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


# -- reading one part -------------------------------------------------------------------
def _gene(text: str, hpo) -> Part | None:
    t = text.strip()
    if CURIE.match(t) and t.split(":", 1)[0].upper() in GENE_PREFIXES:
        sym = (hpo.gene_symbol(t) or _hgnc_by_id(t)
               or (t.split(":", 1)[1].upper() if t.upper().startswith("SYMBOL:") else None))
        return Part(text, "gene", sym, sym, "id") if sym else None
    # a symbol is one token, written like one: "FBN1", "Pmm2", "CFTR" (not "cat")
    if " " in t or len(t) > 15 or not (t.isupper() or any(c.isdigit() for c in t)):
        return None
    sym = hpo.gene_symbol(t)
    if sym:
        return Part(text, "gene", sym, sym, "symbol")
    hits = {(s.upper(), k) for s, _, k in _hgnc(t)}
    symbols = {s for s, _ in hits if hpo.gene_diseases(s)}
    if len(symbols) == 1:  # an alias / previous symbol of exactly one disease gene
        sym = symbols.pop()
        return Part(text, "gene", sym, sym, "alias")
    return None


def _disease(text: str, hpo) -> Part | None:
    """Exact disease name: Orphanet, then HPO's OMIM/ORPHA names, then MONDO."""
    t = text.strip()
    if CURIE.match(t):
        prefix = t.split(":", 1)[0].upper()
        if prefix == "HP" or prefix in GENE_PREFIXES:
            return None
        cid = t.replace("ORPHANET:", "ORPHA:").replace("Orphanet:", "ORPHA:")
        return Part(text, "disease", cid, hpo.dname.get(cid, t), "id")
    orpha = _orpha_names(t)
    if orpha:
        cid, name, typ = orpha[0]
        return Part(text, "disease", cid, name, "name", alternative=typ)
    low = _norm(t)
    for cid, name in hpo.dname.items():
        if name.lower() == low:
            return Part(text, "disease", cid, name, "name")
    for mondo, label in _mondo_names(t):
        ids = [i for i in _mondo_to_hpo_ids(mondo) if i in hpo.ann] or [mondo]
        return Part(text, "disease", ids[0], label, "name", alternative="MONDO")
    return None


def _phenotype(text: str, hpo, approximate: bool) -> Part | None:
    r = hpo.resolve(text)
    if r and r[0] in hpo.descendants(_hpoa.PHENOTYPE_ROOT):
        if r[2] != "approximate" or approximate:
            return Part(text, "phenotype", r[0], r[1], r[2])
    return None


def _words_of(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def _phenotype_loose(text: str, hpo) -> Part | None:
    """symptom_anchor: the HPO reading of a --symptoms item that the exact lookup misses:
    punctuation as spaces ("cone-rod dystrophy" -> HPO "cone rod dystrophy"), else an
    approximate match whose label contains the item's words in order (not "rod-cone")."""
    t = " ".join(re.sub(r"[-/_]", " ", text).split())
    p = _phenotype(t, hpo, approximate=False) if t != text.strip() else None
    if p is None:
        p = _phenotype(t, hpo, approximate=True)
        if p and f" {' '.join(_words_of(t))} " not in f" {' '.join(_words_of(p.label))} ":
            p = None
    if p:
        p.text = text
    return p


def _suggestion(text: str, hpo) -> Part | None:
    """symptom_anchor: an unrecognised --symptoms item whose every word (>= 4 letters)
    starts a word of a suggested HPO term or its matched synonym, or the reverse
    ("stereotypic hand wringing" -> "Stereotypical hand wringing")."""
    q = [w for w in _words_of(text) if len(w) >= 4]
    if len(q) < 2:
        return None
    for hp, name, matched in hpo.suggest(text):
        ws = _words_of(name) + _words_of(matched)
        if all(any(a.startswith(w) or (w.startswith(a) and len(a) >= 5) for a in ws)
               for w in q):
            return Part(text, "phenotype", hp, name, "approximate")
    return None


def read_part(text: str, hpo, approximate: bool = True,
              prefer_phenotype: bool = False) -> Part:
    gene = _gene(text, hpo)
    if gene:
        return gene
    dis = _disease(text, hpo)
    phen = _phenotype(text, hpo, approximate=False)
    if dis and not phen and prefer_phenotype and improvements.on("symptom_anchor"):
        phen = _phenotype_loose(text, hpo)
    if dis and phen:
        # both: Orphanet's type decides (diseases stay diseases, groups / anomalies are
        # read as the sign); a MONDO / OMIM-only name is read as the phenotype
        orpha_type = dis.alternative if dis.id.startswith("ORPHA:") else None
        if orpha_type in ORPHA_DISEASE_TYPES and not prefer_phenotype:
            dis.alternative = f"also the HPO phenotype {phen.label} ({phen.id})"
            return dis
        phen.alternative = f"also the disease {dis.label} ({dis.id})"
        return phen
    if dis:
        dis.alternative = None
        return dis
    if phen:
        return phen
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if approximate and len(tokens) >= 2 and not set(tokens) & DISEASE_WORDS:
        phen = _phenotype(text, hpo, approximate=True)
        if phen:
            return phen
    if prefer_phenotype and improvements.on("symptom_anchor"):
        phen = _suggestion(text, hpo)
        if phen:
            return phen
    return Part(text, "unknown", suggestions=[(hp, name) for hp, name, _ in hpo.suggest(text)])


def interpret(text: str, hpo=None) -> Interpretation:
    """Read the whole input as one thing if it is a known name, else its parts."""
    hpo = hpo or _hpoa.load()
    whole = read_part(text, hpo, approximate=False)
    parts_text = [p for p in SPLIT.split(text) if p.strip()]
    if whole.kind != "unknown":
        return Interpretation(text, [whole])
    if len(parts_text) < 2:  # one unrecognised phrase: a symptom phrase, or "x and y"
        return Interpretation(text, _read_split(text, hpo))
    return Interpretation(text, [q for p in parts_text for q in _read_split(p, hpo)])


def _read_split(text: str, hpo, prefer_phenotype: bool = False) -> list[Part]:
    """One part, or its " and " pieces when the part is not recognised (or only
    approximately) and every piece is recognised exactly."""
    part = read_part(text, hpo, prefer_phenotype=prefer_phenotype)
    if part.kind != "unknown" and part.how != "approximate":
        return [part]
    pieces = [x for x in SPLIT_AND.split(text) if x.strip()]
    if len(pieces) > 1:
        read = [read_part(x, hpo, approximate=False, prefer_phenotype=prefer_phenotype)
                for x in pieces]
        if all(r.kind != "unknown" for r in read):
            return read
    return [part]


@lru_cache(maxsize=None)
def orphanet_assoc(disease: str, symbol: str) -> str | None:
    """Orphanet's gene-disease association type ("Disease-causing germline mutation(s)
    in", "Major susceptibility factor in", ...), or None (not Orphanet / no index)."""
    db = _db("orphadata")
    if db is None or not disease.startswith("ORPHA:"):
        return None
    try:
        rows = db.execute("SELECT assoc FROM gene WHERE code = ? AND symbol = ?",
                          (disease.split(":", 1)[1], symbol)).fetchall()
    except sqlite3.Error:
        return None
    for (a,) in rows:
        try:
            t = json.loads(a).get("DisorderGeneAssociationType")
        except (ValueError, AttributeError):
            continue
        if isinstance(t, str):
            return t
    return None


def orphanet_ids(ids: list[str], name: str) -> list[str]:
    """Orphanet codes of OMIM diseases: the entry with the same name (Marfan syndrome
    OMIM:154700 -> ORPHA:558, not its exact mapping, the subtype "Marfan syndrome type 1"),
    else the exact mappings. Candidates get them before they are expanded, so the
    Orphanet-keyed sources (patient organisations, registries, ...) see the disease
    itself rather than a subtype."""
    db = _db("orphadata")
    omim = [i.split(":", 1)[1] for i in ids if i.startswith("OMIM:")]
    if db is None or not omim:
        return []
    try:
        rows = db.execute(
            f"SELECT x.code, x.relation, d.name FROM oxref x JOIN disorder d ON d.code = x.code "
            f"WHERE x.source = 'OMIM' AND x.reference IN ({','.join('?' * len(omim))})",
            omim).fetchall()
    except sqlite3.Error:
        return []
    same = [c for c, _, n in rows if _norm(n) == _norm(name)]
    exact = [c for c, rel, _ in rows if (rel or "").startswith("E")]
    return [f"ORPHA:{c}" for c in dict.fromkeys(same or exact)]


def rank(interp: Interpretation, top: int, hpo=None) -> list[dict]:
    """Candidate diseases of a "candidates" interpretation (_hpoa.rank_diseases); diseases
    named in the input come first (pinned=True)."""
    hpo = hpo or _hpoa.load()
    ranking = hpo.rank_diseases([p.id for p in interp.of("phenotype")], top=top + 5,
                                genes=[p.id for p in interp.of("gene")],
                                gene_assoc=orphanet_assoc,
                                coverage=COVERAGE if improvements.on("annotation_coverage")
                                else 0.0)
    pinned = []
    for p in interp.of("disease"):
        hit = next((r for r in ranking if p.id in (r["id"], *r["xrefs"])), None)
        if hit:
            ranking.remove(hit)
        else:
            hit = {"id": p.id, "name": p.label or p.id, "score": 0.0, "matches": [],
                   "xrefs": [], "genes": []}
        pinned.append({**hit, "pinned": True})
    if improvements.on("symptom_anchor"):
        return dedupe_ranking(pinned + ranking)[:max(top, len(pinned))]
    return (pinned + ranking)[:max(top, len(pinned))]


def _fold(s: str) -> str:
    t = unicodedata.normalize("NFKD", s or "")
    return " ".join(_words_of("".join(c for c in t if not unicodedata.combining(c))))


def _mondo_of(ids: list[str]) -> list[str]:
    """MONDO ids with an exact xref to one of the OMIM / ORPHA ids (local ontology index)."""
    db = _db("ontology")
    want = []
    for i in ids:
        p, _, x = i.partition(":")
        if p.upper() == "OMIM":
            want += [f"OMIM:{x}", f"MIM:{x}"]
        elif p.upper() in ("ORPHA", "ORPHANET"):
            want += [f"Orphanet:{x}", f"ORPHA:{x}", f"ORPHANET:{x}"]
    if db is None or not want:
        return []
    try:
        # COLLATE NOCASE: the index on xref is case-insensitive (else a full table scan)
        rows = db.execute(f"SELECT DISTINCT curie FROM xref WHERE xref COLLATE NOCASE IN "
                          f"({','.join('?' * len(want))}) AND curie LIKE 'MONDO:%'",
                          want).fetchall()
    except sqlite3.Error:
        return []
    return [r[0] for r in rows]


def identity_keys(r: dict) -> set[str]:
    """Ids a ranked disease is known by (its OMIM / ORPHA ids, the Orphanet entry of the
    same name, exact MONDO mappings) and its accent-folded name."""
    ids = [r["id"], *(r.get("xrefs") or [])]
    ids += orphanet_ids(ids, r.get("name") or "")
    ids += _mondo_of(ids)
    return set(ids) | {"name:" + _fold(r.get("name") or "")}


def dedupe_ranking(ranking: list[dict]) -> list[dict]:
    """symptom_anchor: one entry per disease: a later entry sharing an id or folded name
    with an earlier one (Alström syndrome ORPHA:64 / Alstrom syndrome OMIM:203800) is
    merged into it (ids as xrefs, genes joined)."""
    out: list[tuple[set[str], dict]] = []
    for r in ranking:
        ks = identity_keys(r)
        hit = next((x for x in out if x[0] & ks), None)
        if hit is None:
            out.append((ks, {**r, "xrefs": list(r.get("xrefs") or [])}))
            continue
        keys, first = hit
        keys |= ks
        first["xrefs"] += [i for i in [r["id"], *(r.get("xrefs") or [])]
                           if i != first["id"] and i not in first["xrefs"]]
        first["genes"] = list(dict.fromkeys([*(first.get("genes") or []),
                                             *[tuple(g) for g in r.get("genes") or []]]))
        first["pinned"] = bool(first.get("pinned") or r.get("pinned"))
    return [r for _, r in out]


def focus_gate(ranking: list[dict], n: int, n_phen: int, coverage: float = 2 / 3,
               ratio: float = 0.85) -> list[dict]:
    """symptom_anchor: the candidates that get a full profile: pinned ones (named in the
    input), the top one, and further ones (up to n in all) only when they match at least
    `coverage` of the top's fully matched symptoms and score at least `ratio` of it."""
    def full(r):
        return sum(1 for m in r.get("matches") or [] if m[2])
    pinned = [r for r in ranking if r.get("pinned")]
    rest = [r for r in ranking if not r.get("pinned")]
    if not rest:
        return pinned[:max(n, len(pinned))]
    top = rest[0]
    out = pinned + [top]
    for r in rest[1:]:
        if len(out) >= n:
            break
        if n_phen and full(r) < coverage * full(top):
            continue
        if r["score"] < ratio * top["score"]:
            continue
        out.append(r)
    return out[:max(n, len(pinned))]


def interpret_list(items: list[str], hpo=None, prefer_phenotype: bool = False) -> Interpretation:
    """--symptoms / --as candidates: every item (or comma / semicolon separated piece) is
    one part; prefer_phenotype reads names that are also HPO terms as the phenotype."""
    hpo = hpo or _hpoa.load()
    texts = [t for s in items for t in SPLIT.split(s) if t.strip()]
    return Interpretation("; ".join(texts), [q for t in texts
                                             for q in _read_split(t, hpo, prefer_phenotype)])
