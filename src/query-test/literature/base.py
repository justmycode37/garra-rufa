"""Shared types for the literature pipeline.

Paper      one publication, identified by PMID / DOI / PMCID (any of them), with the
           metadata later steps need to link and sort papers, and the `hits` that say
           which provider found it for which graph entity and why.
Hit        one reason a paper is in the collection (provider, query, entity, relation,
           rank in that provider's result list).
Concept    a graph entity as the providers need it: names, MeSH heading, gene symbol,
           rsID, ... (built by plan.py from the graph JSON of main.py).
Query      what to search: a disease concept, optionally combined with a second concept
           (disease + gene, disease + drug, ...), with a relation name and a result cap.
Provider   base class of the literature sources (pubmed, europepmc, litvar, pubtator):
           search(query) -> papers. Errors are swallowed (logged in stats), like the graph
           sources do.
Cache      raw API responses in data/literature-cache/cache.sqlite (keyed by URL +
           params), so reruns cost no requests. --refresh ignores it.
"""
import hashlib
import json
import sqlite3
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

TIMEOUT = 60
USER_AGENT = "garra-rufa-literature/0.1"
CACHE_PATH = Path(__file__).resolve().parents[3] / "data" / "literature-cache" / "cache.sqlite"
RETRIES = 3  # on HTTP 429 / 5xx, with 1/2/4 s backoff


# -- ids -------------------------------------------------------------------------
def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = str(doi).strip()
    for p in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "doi:", "DOI:"):
        if d.lower().startswith(p.lower()):
            d = d[len(p):]
    return d.lower().rstrip(".,;") if d.startswith("10.") else None


def norm_pmcid(pmcid) -> str | None:
    s = str(pmcid or "").strip().upper()
    if s.startswith("PMC") and s[3:].isdigit():
        return s
    return f"PMC{s}" if s.isdigit() else None


def norm_pmid(pmid) -> str | None:
    s = str(pmid or "").strip().upper().removeprefix("PMID:")
    return str(int(s)) if s.isdigit() and int(s) > 0 else None


# -- data model --------------------------------------------------------------------
@dataclass
class Hit:
    provider: str  # pubmed | europepmc | pubtator | litvar | graph:<source>
    query: str  # the query string (or, for graph hits, what cited it)
    entity: str  # graph node id the search was for ("A|B" for a combination)
    relation: str  # disease, disease+gene, variant, cited_for_entity, evidence:<rel>, ...
    rank: int | None = None  # position in the provider's result list (0 = first)


@dataclass(eq=False)
class Paper:
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    title: str | None = None
    abstract: str | None = None
    journal: str | None = None
    year: int | None = None
    pub_types: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    mesh: list[str] = field(default_factory=list)  # "*" prefix = major topic
    keywords: list[str] = field(default_factory=list)
    cited_by: int | None = None
    open_access: bool | None = None
    preprint: bool | None = None
    annotations: list[dict] = field(default_factory=list)  # PubTator concepts
    relations: list[str] = field(default_factory=list)  # PubTator "type|@A|@B"
    hits: list[Hit] = field(default_factory=list)

    def ids(self) -> list[str]:
        return [k for k in (self.pmid and f"PMID:{self.pmid}", self.doi and f"DOI:{self.doi}",
                            self.pmcid and f"PMC:{self.pmcid}") if k]

    def merge(self, other: "Paper"):
        """Fill missing fields from `other`, union lists, add its hits."""
        for k in ("pmid", "pmcid", "doi", "title", "abstract", "journal", "year",
                  "open_access", "preprint"):
            if getattr(self, k) in (None, "") and getattr(other, k) not in (None, ""):
                setattr(self, k, getattr(other, k))
        if other.cited_by is not None:
            self.cited_by = max(self.cited_by or 0, other.cited_by)
        for k in ("pub_types", "authors", "mesh", "keywords"):
            if not getattr(self, k):
                setattr(self, k, list(getattr(other, k)))
        if other.annotations and not self.annotations:
            self.annotations, self.relations = other.annotations, other.relations
        seen = {(h.provider, h.query, h.entity) for h in self.hits}
        self.hits += [h for h in other.hits if (h.provider, h.query, h.entity) not in seen]

    def to_json(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, [], "")}


class Collection:
    """Papers deduplicated by any shared id (PMID, DOI, PMCID)."""

    def __init__(self):
        self.papers: list[Paper] = []
        self._by_id: dict[str, Paper] = {}

    def __len__(self):
        return len(self.papers)

    def get(self, key: str) -> Paper | None:
        return self._by_id.get(key)

    def add(self, p: Paper) -> bool:
        """Add or merge; True if the paper is new."""
        p.doi = norm_doi(p.doi)
        p.pmcid = norm_pmcid(p.pmcid)
        p.pmid = norm_pmid(p.pmid)
        found = list({id(x): x for x in (self._by_id.get(k) for k in p.ids()) if x}.values())
        if not found:
            if not p.ids():
                return False
            self.papers.append(p)
            for k in p.ids():
                self._by_id[k] = p
            return True
        keep = found[0]
        ids = set(p.ids())
        for other in [p, *found[1:]]:  # p's ids may join two papers known so far
            keep.merge(other)
            ids |= set(other.ids())
            if other is not p:
                self.papers = [x for x in self.papers if x is not other]
        for k in ids | set(keep.ids()):
            self._by_id[k] = keep
        return False


@dataclass
class Concept:
    """A graph entity, with what the providers search it by."""
    id: str
    label: str
    kind: str  # disease | gene | drug | phenotype | variant
    names: list[str] = field(default_factory=list)  # label + exact synonyms (no abbrev.)
    mesh: str | None = None  # MeSH heading (diseases, drugs, phenotypes)
    mesh_exact: bool = False  # heading names this entity (not a broader descriptor)
    mesh_id: str | None = None  # D/C id of that heading
    symbol: str | None = None  # gene symbol
    ncbigene: str | None = None
    rsid: str | None = None  # variants
    gene: str | None = None  # variant: its gene symbol
    hgvs: str | None = None  # variant: protein change, e.g. p.Met1Lys
    support: int = 0  # how many graph sources link it to the disease


def gene_terms(c: Concept) -> list[str]:
    """Free-text names of a gene concept. Symbols of up to 3 letters or that read as words
    ("CAT", "CBS", "SET", "MAX") would match unrelated text, so they are replaced by the
    gene's full name."""
    sym = c.symbol or c.label
    risky = len(sym) <= 3 or sym.isalpha() and sym.upper() in AMBIGUOUS
    return [x for x in ([] if risky else [sym]) + [n for n in c.names if n != sym]][:3]


AMBIGUOUS = {"CAT", "SET", "MAX", "REST", "WAS", "CAN", "MET", "KIT", "RET", "SHE", "CLOCK",
             "IMPACT", "STAR", "AIRE", "HR", "PIGS", "TANK", "FAST", "LARGE", "NOV", "MARS",
             "COIL", "SPINK", "GAL", "CA", "AR", "APP", "TH", "CD", "BAD", "BID", "ARMS"}


@dataclass
class Query:
    id: str
    relation: str  # disease, disease_reviews, disease_most_cited, disease+gene, variant, ...
    disease: Concept | None
    other: Concept | None = None
    cap: int = 50  # max results per provider
    qualifier: str | None = None  # MeSH subheading (disease_subheading: genetics, therapy, ...)

    @property
    def entity(self) -> str:
        ids = [c.id for c in (self.disease, self.other) if c]
        return "|".join(ids)


# -- HTTP: cache, throttle, retries -------------------------------------------------
class Throttle:
    """Minimum spacing between requests to one host (shared by all users)."""

    def __init__(self, interval: float):
        self.interval = interval
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            delay = self.interval - (time.monotonic() - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


class Cache:
    def __init__(self, path: Path = CACHE_PATH, refresh: bool = False):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)  # guarded by _lock
        self.db.execute("CREATE TABLE IF NOT EXISTS r (k TEXT PRIMARY KEY, url TEXT, "
                        "body TEXT, t REAL)")
        self.refresh = refresh
        self.hits = self.misses = 0
        self._lock = threading.Lock()

    @staticmethod
    def key(method: str, url: str, params, data) -> str:
        params = {k: v for k, v in (params or {}).items() if k != "api_key"}
        raw = json.dumps([method, url, params, data], sort_keys=True, default=str)
        return hashlib.sha1(raw.encode()).hexdigest()

    def get(self, k: str) -> str | None:
        if self.refresh:
            return None
        with self._lock:
            row = self.db.execute("SELECT body FROM r WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def put(self, k: str, url: str, body: str):
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO r VALUES (?,?,?,?)",
                            (k, url, body, time.time()))
            self.db.commit()


class Provider:
    """A literature source. Subclasses set `name` and implement search()."""
    name = "base"
    throttle: Throttle | None = None
    # relations this provider answers (plan.py relation names); None = all
    relations: frozenset[str] | None = None

    def __init__(self, cache: Cache):
        self.cache = cache
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.errors: list[str] = []

    def handles(self, q: Query) -> bool:
        return self.relations is None or q.relation in self.relations

    def search(self, q: Query) -> tuple[str, list[Paper]]:
        """(query string as sent, papers with one Hit each). Never raises."""
        raise NotImplementedError

    def enrich(self, coll: Collection):
        """Fill metadata of papers other providers found (optional)."""

    def hit(self, query: str, q: Query, rank: int) -> Hit:
        return Hit(self.name, query, q.entity, q.relation, rank)

    # -- requests ----------------------------------------------------------------
    def fetch(self, url: str, params: dict | None = None, data: dict | None = None,
              method: str = "GET", cache: bool = True) -> str:
        """Response text, from the cache when possible; raises after RETRIES."""
        k = Cache.key(method, url, params, data)
        if cache:
            body = self.cache.get(k)
            if body is not None:
                self.cache.hits += 1
                return body
        self.cache.misses += 1
        for attempt in range(RETRIES + 1):
            if self.throttle:
                self.throttle.wait()
            r = self.session.request(method, url, params=params, data=data, timeout=TIMEOUT)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < RETRIES:
                time.sleep(float(r.headers.get("Retry-After") or 0) or 2 ** attempt)
                continue
            r.raise_for_status()
            if cache:
                self.cache.put(k, url, r.text)
            return r.text
        raise RuntimeError("unreachable")

    def fetch_json(self, url: str, params: dict | None = None, **kw):
        return json.loads(self.fetch(url, params, **kw))

    def fail(self, what: str, e: Exception):
        msg = f"{self.name}: {what}: {type(e).__name__} {str(e)[:160]}"
        self.errors.append(msg)
        print(f"  ! {msg}", file=sys.stderr)
