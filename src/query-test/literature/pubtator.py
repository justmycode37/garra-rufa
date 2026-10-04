"""PubTator 3 (https://www.ncbi.nlm.nih.gov/research/pubtator3/), no key, <= 3 requests/s.

PubTator normalises genes, diseases, chemicals, variants, species and cell lines in all of
PubMed (and the PMC open-access subset) to ids, so a concept search finds papers whatever
name they use, and every paper comes with its entity annotations.

  /entity/autocomplete/?query=..&concept=disease|gene|chemical   name -> @DISEASE_.. id
  /search/?text=@DISEASE_x AND @GENE_y&page=..&size=100           papers (relevance)
  /relations?e1=@DISEASE_x                                        co-mentioned concepts with
                                                                  relation type and paper counts
  /publications/export/biocjson?pmids=..                          annotations, 100 per request

Concepts are matched conservatively: a gene must match its symbol (or NCBI Gene id), a
disease its MeSH id or one of its names; otherwise the query is skipped (a wrong concept
would pull in an unrelated literature). Phenotypes are PubTator "disease" concepts.

enrich() exports the annotations of every PMID in the collection: per paper the distinct
concepts (type, PubTator id, database id, name, mentions) and the relations PubTator
extracted (type|concept|concept), the input for linking papers later.
"""
from . import ncbi
from .base import Collection, Concept, Paper, Provider, Query
from .pubmed import _local_first

API = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api"
BIOTYPE = {"disease": "disease", "phenotype": "disease", "gene": "gene", "drug": "chemical"}
PAGE = 100
BATCH = 100


class PubtatorProvider(Provider):
    name = "pubtator"
    throttle = ncbi.RESEARCH
    relations = frozenset({"disease", "disease+gene", "disease+drug", "disease+phenotype",
                           "subtype"})

    def __init__(self, cache):
        super().__init__(cache)
        self._concepts: dict[str, str | None] = {}
        self.related: dict[str, list[dict]] = {}  # disease entity -> PubTator relations

    # -- concepts --------------------------------------------------------------
    def concept(self, c: Concept) -> str | None:
        if c.id in self._concepts:
            return self._concepts[c.id]
        bt = BIOTYPE.get(c.kind)
        found = None
        terms = [c.symbol] if c.kind == "gene" and c.symbol else \
            list(dict.fromkeys([*([c.mesh] if c.mesh and c.mesh_exact else []), *c.names[:6]]))
        for t in terms if bt else []:
            try:
                rows = self.fetch_json(f"{API}/entity/autocomplete/",
                                       {"query": t, "concept": bt, "limit": 5}) or []
            except Exception as e:
                self.fail(f"autocomplete {t}", e)
                break
            for r in rows:
                name = r.get("name") or ""
                if c.kind == "gene":
                    ok = name.upper() == (c.symbol or "").upper() or (
                        c.ncbigene and str(r.get("db_id")) == c.ncbigene)
                else:
                    # A broader MeSH mapping and autocomplete rank are not identity evidence.
                    names = [*c.names, *([c.mesh] if c.mesh and c.mesh_exact else [])]
                    ok = (c.mesh_exact and c.mesh_id and r.get("db_id") == c.mesh_id) or (
                        " ".join(name.casefold().split()) in {
                            " ".join(n.casefold().split()) for n in names if n
                        }
                    )
                if ok:
                    found = r["_id"]
                    break
            if found:
                break
        self._concepts[c.id] = found
        return found

    # -- search ------------------------------------------------------------------
    def search(self, q: Query) -> tuple[str, list[Paper]]:
        parts = [q.other] if q.relation == "subtype" else [q.disease, q.other]
        ids = [self.concept(c) for c in parts if c]
        if not ids or not all(ids):
            return "", []
        text = " AND ".join(ids)
        out: list[Paper] = []
        page = 1
        try:
            while len(out) < q.cap:
                d = self.fetch_json(f"{API}/search/", {"text": text, "page": page,
                                                       "size": PAGE})
                rows = d.get("results") or []
                for r in rows:
                    if len(out) >= q.cap:
                        break
                    date = str(r.get("date") or "")[:4]
                    out.append(Paper(pmid=str(r.get("pmid") or ""), pmcid=r.get("pmcid"),
                                     doi=r.get("doi"), title=r.get("title"),
                                     journal=r.get("journal"),
                                     year=int(date) if date.isdigit() else None,
                                     authors=r.get("authors") or [],
                                     hits=[self.hit(text, q, len(out))]))
                if page >= (d.get("total_pages") or 1) or not rows:
                    break
                page += 1
        except Exception as e:
            self.fail(f"search {text}", e)
        if q.relation == "disease" and q.disease.id not in self.related:
            self.related[q.disease.id] = self._relations(ids[0])
        return text, out

    def _relations(self, cid: str) -> list[dict]:
        try:
            return self.fetch_json(f"{API}/relations", {"e1": cid}) or []
        except Exception as e:
            self.fail(f"relations {cid}", e)
            return []

    # -- annotations ---------------------------------------------------------------
    def enrich(self, coll: Collection):
        todo = [p.pmid for p in coll.papers if p.pmid and not p.annotations]
        done = 0
        for batch in _local_first(todo, BATCH):
            try:
                d = self.fetch_json(f"{API}/publications/export/biocjson",
                                    {"pmids": ",".join(batch)})
            except Exception as e:
                self.fail(f"export {len(batch)} pmids", e)
                continue
            finally:
                done += len(batch)
            for doc in (d.get("PubTator3") if isinstance(d, dict) else d) or []:
                p = coll.get(f"PMID:{doc.get('pmid') or doc.get('id')}")
                if p is not None:
                    p.annotations, p.relations = summarize(doc)
            print(f"  pubtator annotations {done}/{len(todo)}", flush=True)


def summarize(doc: dict) -> tuple[list[dict], list[str]]:
    """Distinct concepts of a BioC document with mention counts, and its relations."""
    concepts: dict[str, dict] = {}
    for passage in doc.get("passages") or []:
        for a in passage.get("annotations") or []:
            inf = a.get("infons") or {}
            key = inf.get("accession") or f"{inf.get('type')}:{inf.get('identifier')}"
            if not key or inf.get("type") == "Species" and inf.get("identifier") == "9606":
                continue  # "patients" / "human" in nearly every paper
            c = concepts.setdefault(key, {"type": inf.get("type"), "id": key,
                                          "db_id": inf.get("identifier"),
                                          "name": inf.get("name") or a.get("text"), "n": 0})
            c["n"] += 1
    rels = [r.get("name") for r in doc.get("relations_display") or [] if r.get("name")]
    return sorted(concepts.values(), key=lambda c: -c["n"]), rels
