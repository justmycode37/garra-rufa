"""RARe-SOURCE (NIH NCATS / Frederick National Lab, https://raresource.nih.gov).

Integrated rare-disease resource: ~7,200 genetic rare diseases with their causative
genes (~4,500), aliases and cross-references. There is no public API or bulk download;
the JSON/AJAX endpoints (/diseases/disease_info/, /genes/gene_info/, ...) are disallowed
in robots.txt, and the per-disease pages need a login. But the two browse pages are
server-rendered and contain the whole catalogue as one HTML table each:
  https://raresource.nih.gov/diseases/   (~11 MB) name, aliases ("//"-separated), genes
      (";"-separated symbols), GARD id, OMIM, Orphanet, UMLS, MeSH
  https://raresource.nih.gov/genes/      (~4.5 MB) symbol, description, associated
      diseases ("name?id;..."), NCBI gene id, Ensembl id, HGNC id
Both are downloaded once into <repo>/data/raresource/ (gitignored) and parsed in memory
with the stdlib, so plain requests is enough (no JS rendering, no bot protection).
Delete the files to refresh.

The disease id column is the 7-digit GARD id (0016535 = Marfan syndrome, the site links it
to rarediseases.info.nih.gov/diseases/16535), so disease nodes get GARD:<id> and carry
OMIM/ORPHA/UMLS/MESH xrefs.

Coverage: the two tables are the full disease-gene catalogue (7,200 diseases, 4,512
genes; they cross-reference each other completely). Not included: variants, curated
literature and genotype-phenotype data, which sit behind AJAX endpoints that robots.txt
disallows or behind an NIH login.

Node handling: free text -> diseases whose name or alias equals the term, else whose
name contains it ("matches"); GARD:/ORPHA:/OMIM: -> the disease ("matches") and its
genes ("caused_by_gene"); HGNC:/NCBIGene:/ENSEMBL:/SYMBOL: genes -> diseases
("causes_disease"). RARe-SOURCE only lists genes with a known causative association.
"""
import html
import re
import sys
import threading
from pathlib import Path

from .base import Edge, Node, Source

BASE = "https://raresource.nih.gov"
PAGES = {"diseases.html": "/diseases/", "genes.html": "/genes/"}
DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "raresource"

_TBODY = re.compile(r"<tbody[^>]*>(.*?)</tbody>", re.S)
_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

# column indices of the browse tables
D_NAME, D_ALIASES, D_GENES, D_ID, D_OMIM, D_ORPHA, D_UMLS, D_MESH = 1, 2, 3, 6, 7, 8, 9, 10
G_SYMBOL, G_DESC, G_DISEASES, G_NCBI, G_ENSEMBL, G_HGNC = 1, 2, 3, 6, 7, 8


def _text(cell: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub("", cell))).strip()


def _rows(page: str) -> list[list[str]]:
    m = _TBODY.search(page)
    if not m:
        raise ValueError("no <tbody> in RARe-SOURCE page (layout changed?)")
    return [[_text(c) for c in _TD.findall(r)] for r in _TR.findall(m.group(1))]


def _split(s: str, sep: str) -> list[str]:
    return [x.strip() for x in s.split(sep) if x.strip() and x.strip() != "None"]


class RareSourceSource(Source):
    name = "raresource"
    id_prefixes = frozenset({"GARD", "ORPHA", "ORPHANET", "OMIM", "MIM",
                             "HGNC", "NCBIGENE", "ENSEMBL", "SYMBOL"})
    by_name = True

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._loaded = False
        self._failed = False
        self._diseases: dict[str, dict] = {}         # rs id -> record
        self._by_xref: dict[str, list[str]] = {}     # "ORPHA:558"/"OMIM:154700" -> rs ids
        self._genes: dict[str, dict] = {}            # symbol -> record
        self._gene_by_id: dict[str, str] = {}        # "HGNC:3603"/"NCBIGene:2200"/... -> symbol
        self._gene_diseases: dict[str, list[str]] = {}  # symbol -> rs ids

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            self._load()
            return self._query(node, limit)
        except Exception:
            return []

    # -- data preparation ------------------------------------------------
    def _download(self, fname: str) -> str:
        path = DATA_DIR / fname
        if not path.exists():
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            print(f"raresource: downloading {BASE}{PAGES[fname]} ...", file=sys.stderr)
            r = self.session.get(BASE + PAGES[fname], timeout=120)
            r.raise_for_status()
            part = path.with_suffix(path.suffix + ".part")
            part.write_text(r.text, encoding="utf-8")
            part.replace(path)
        return path.read_text(encoding="utf-8")

    def _load(self):
        with self._lock:
            if self._loaded:
                return
            if self._failed:
                raise RuntimeError("raresource data unavailable")
            try:
                self._parse_diseases(self._download("diseases.html"))
                self._parse_genes(self._download("genes.html"))
            except Exception as e:
                self._failed = True  # don't re-download for every node
                print(f"raresource: load failed: {e}", file=sys.stderr)
                raise
            self._loaded = True

    def _parse_diseases(self, page: str):
        for c in _rows(page):
            if len(c) <= D_MESH or not c[D_ID]:
                continue
            rid = c[D_ID]
            xrefs = []
            for pre, col in (("OMIM", D_OMIM), ("ORPHA", D_ORPHA), ("UMLS", D_UMLS),
                             ("MESH", D_MESH)):
                xrefs += [f"{pre}:{v}" for v in _split(c[col], ";")]
            self._diseases[rid] = {"name": c[D_NAME], "aliases": _split(c[D_ALIASES], "//"),
                                   "genes": _split(c[D_GENES], ";"), "xrefs": tuple(xrefs)}
            for x in xrefs:
                if x.startswith(("OMIM:", "ORPHA:")):
                    self._by_xref.setdefault(x, []).append(rid)

    def _parse_genes(self, page: str):
        for c in _rows(page):
            if len(c) <= G_HGNC or not c[G_SYMBOL]:
                continue
            sym = c[G_SYMBOL]
            xrefs = [f"{pre}:{c[col]}" for pre, col in
                     (("HGNC", G_HGNC), ("NCBIGene", G_NCBI), ("ENSEMBL", G_ENSEMBL))
                     if c[col] and c[col] != "None"]
            self._genes[sym] = {"desc": c[G_DESC], "xrefs": tuple(xrefs)}
            for x in xrefs:
                self._gene_by_id[x.upper()] = sym
            # "name?0027281;name?0015081": the id after the last "?" is the disease id
            self._gene_diseases[sym] = [d.rsplit("?", 1)[1] for d in _split(c[G_DISEASES], ";")
                                        if "?" in d]
        # genes only named in the disease table still get disease links
        for rid, d in self._diseases.items():
            for sym in d["genes"]:
                rids = self._gene_diseases.setdefault(sym, [])
                if rid not in rids:
                    rids.append(rid)

    # -- nodes -----------------------------------------------------------
    def _disease(self, rid: str) -> Node:
        d = self._diseases[rid]
        return Node(d["name"], id=f"GARD:{rid}", kind="disease", source=self.name,
                    xrefs=d["xrefs"])

    def _gene(self, sym: str) -> Node:
        g = self._genes.get(sym)
        xrefs = g["xrefs"] if g else ()
        hgnc = next((x for x in xrefs if x.startswith("HGNC:")), None)
        rest = tuple(x for x in xrefs if x != hgnc) + (f"SYMBOL:{sym}",)
        return Node(sym, id=hgnc or f"SYMBOL:{sym}", kind="gene", source=self.name,
                    xrefs=rest if hgnc else rest[:-1])

    # -- dispatch --------------------------------------------------------
    def _query(self, node: Node, limit: int) -> list[Edge]:
        if node.id is None:
            return self._search(node, limit)
        if node.kind == "gene" or any(c.split(":", 1)[0].upper() in
                                      ("HGNC", "NCBIGENE", "ENSEMBL", "SYMBOL")
                                      for c in self.ids_for(node)):
            return self._gene_query(node, limit)
        return self._disease_query(node, limit)

    def _search(self, node: Node, limit: int) -> list[Edge]:
        if node.kind not in ("disease", "unknown", "term"):
            return []
        term = node.label.strip().lower()
        if not term:
            return []
        exact = [rid for rid, d in self._diseases.items()
                 if d["name"].lower() == term or term in (a.lower() for a in d["aliases"])]
        hits = exact or [rid for rid, d in self._diseases.items() if term in d["name"].lower()]
        hits.sort(key=lambda rid: len(self._diseases[rid]["name"]))
        return [Edge(node, self._disease(rid), "matches", self.name) for rid in hits[:limit]]

    def _resolve_diseases(self, node: Node) -> list[str]:
        for cand in self.ids_for(node):
            pre, local = cand.split(":", 1)
            pre = pre.upper()
            if pre == "GARD" and local.zfill(7) in self._diseases:
                return [local.zfill(7)]
            if pre in ("ORPHA", "ORPHANET"):
                rids = self._by_xref.get(f"ORPHA:{local}")
            elif pre in ("OMIM", "MIM"):
                rids = self._by_xref.get(f"OMIM:{local}")
            else:
                rids = None
            if rids:
                return rids
        return []

    def _disease_query(self, node: Node, limit: int) -> list[Edge]:
        edges = []
        for rid in self._resolve_diseases(node):
            if not (node.id or "").upper().startswith("GARD:"):
                edges.append(Edge(node, self._disease(rid), "matches", self.name))
            edges += [Edge(node, self._gene(sym), "caused_by_gene", self.name)
                      for sym in self._diseases[rid]["genes"]]
        return edges[:limit]

    def _gene_query(self, node: Node, limit: int) -> list[Edge]:
        sym = None
        for cand in self.ids_for(node):
            pre, local = cand.split(":", 1)
            sym = local if pre.upper() == "SYMBOL" else self._gene_by_id.get(cand.upper())
            if sym:
                break
        if sym is None and node.label in self._gene_diseases:
            sym = node.label  # HGNC/NCBIGene ids unknown to RARe-SOURCE: fall back to symbol
        if sym is None:
            return []
        rids = [r for r in self._gene_diseases.get(sym, []) if r in self._diseases]
        return [Edge(node, self._disease(r), "causes_disease", self.name) for r in rids[:limit]]
