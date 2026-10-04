"""Europe PMC REST API (https://europepmc.org/RestfulWebService), no key.

  /search?query=..&resultType=core&format=json&pageSize=<=1000&cursorMark=*&sort=..

Europe PMC covers PubMed plus preprints (SRC:PPR), PMC full text and Agricola, and
returns citation counts and open-access flags. Its MeSH field is incomplete (a heading
search finds a tenth of what PubMed does), so queries use the names in title/abstract:
  disease             TITLE_ABS:"<name>" OR ... (relevance)
  disease_most_cited  the same, sorted by citation count
  disease_preprints   the same, preprints only
  disease+<kind>      <disease> AND TITLE_ABS:"<symbol / drug / phenotype name>"
  subtype             the subtype's names
  symptom_treatment   a typed symptom's names AND (treatment | therapy | drug)
enrich() looks up every paper of the collection that has no citation count yet (batches
of EXT_ID / DOI / PMCID queries), so all papers get cited_by, open_access, PMCID / DOI.
"""
from .base import Collection, Concept, Paper, Provider, Query, Throttle, gene_terms

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
BATCH = 50


def _q(s: str) -> str:
    return '"' + s.replace('"', "") + '"'


def names_term(c: Concept, n: int = 6) -> str | None:
    names = gene_terms(c) if c.kind == "gene" else (c.names[:n] or [c.label])
    if not names:
        return None
    return "(" + " OR ".join(f"TITLE_ABS:{_q(x)}" for x in dict.fromkeys(names)) + ")"


def parse(r: dict) -> Paper:
    j = (r.get("journalInfo") or {}).get("journal") or {}
    mesh = []
    for h in (r.get("meshHeadingList") or {}).get("meshHeading") or []:
        quals = (h.get("meshQualifierList") or {}).get("meshQualifier") or []
        major = h.get("majorTopic_YN") == "Y" or any(x.get("majorTopic_YN") == "Y" for x in quals)
        mesh.append(("*" if major else "") + h.get("descriptorName", "")
                    + "".join(f"/{x.get('qualifierName')}" for x in quals))
    year = r.get("pubYear")
    return Paper(
        pmid=r.get("pmid"), pmcid=r.get("pmcid"), doi=r.get("doi"), title=r.get("title"),
        abstract=r.get("abstractText"), journal=j.get("isoabbreviation") or j.get("title"),
        year=int(year) if str(year or "").isdigit() else None,
        pub_types=(r.get("pubTypeList") or {}).get("pubType") or [],
        authors=[a.get("fullName") or a.get("collectiveName") or ""
                 for a in (r.get("authorList") or {}).get("author") or []],
        mesh=mesh, keywords=(r.get("keywordList") or {}).get("keyword") or [],
        cited_by=r.get("citedByCount"), open_access=r.get("isOpenAccess") == "Y",
        preprint=r.get("source") == "PPR")


class EuropePmcProvider(Provider):
    name = "europepmc"
    throttle = Throttle(0.1)
    relations = frozenset({"disease", "disease_most_cited", "disease_preprints", "disease+gene",
                           "disease+drug", "disease+phenotype", "subtype",
                           "symptom_treatment"})

    def term(self, q: Query) -> tuple[str, str] | None:
        """(query, sort)"""
        d = q.disease
        if q.relation == "symptom_treatment":  # q.disease is the typed phenotype
            return (f"{names_term(d, 4)} AND (TITLE_ABS:\"treatment\" OR "
                    f"TITLE_ABS:\"therapy\" OR TITLE_ABS:\"drug\")"), ""
        if q.relation == "disease":
            return names_term(d), ""
        if q.relation == "disease_most_cited":
            return names_term(d), "CITED desc"
        if q.relation == "disease_preprints":
            return f"{names_term(d)} AND SRC:PPR", ""
        if q.relation == "subtype":
            return names_term(q.other), ""
        if q.other and names_term(q.other, 3):
            return f"{names_term(d)} AND {names_term(q.other, 3)}", ""
        return None

    def _search(self, query: str, size: int, sort: str = "") -> list[dict]:
        params = {"query": query, "format": "json", "resultType": "core",
                  "pageSize": min(size, 1000), "cursorMark": "*"}
        if sort:
            params["sort"] = sort
        return (self.fetch_json(API, params).get("resultList") or {}).get("result") or []

    def search(self, q: Query) -> tuple[str, list[Paper]]:
        t = self.term(q)
        if not t:
            return "", []
        query, sort = t
        try:
            rows = self._search(query, q.cap, sort)
        except Exception as e:
            self.fail(f"search {query[:80]}", e)
            return query, []
        out = []
        for rank, r in enumerate(rows[:q.cap]):
            p = parse(r)
            p.hits.append(self.hit(query + (f" [sort {sort}]" if sort else ""), q, rank))
            out.append(p)
        return query, out

    def enrich(self, coll: Collection):
        todo = [p for p in coll.papers if p.cited_by is None]
        groups = [("EXT_ID:{} AND SRC:MED", [p.pmid for p in todo if p.pmid]),
                  ("PMCID:{}", [p.pmcid for p in todo if not p.pmid and p.pmcid]),
                  ("DOI:{}", [_q(p.doi) for p in todo if not p.pmid and not p.pmcid and p.doi])]
        done = 0
        for fmt, ids in groups:
            for i in range(0, len(ids), BATCH):
                batch = ids[i:i + BATCH]
                if fmt.startswith("EXT_ID"):
                    query = "EXT_ID:(" + " OR ".join(batch) + ") AND SRC:MED"
                else:
                    query = " OR ".join(fmt.format(x) for x in batch)
                try:
                    for r in self._search(query, len(batch) * 2):
                        coll.add(parse(r))
                except Exception as e:
                    self.fail(f"enrich {len(batch)} ids", e)
                done += len(batch)
            if ids:
                print(f"  europepmc enrich {done}/{len(todo)}", flush=True)
