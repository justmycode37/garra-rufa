"""PubMed via NCBI E-utilities (https://www.ncbi.nlm.nih.gov/books/NBK25499/), no key needed.

  esearch.fcgi?db=pubmed&term=..&sort=relevance   PMIDs for a query (best match first)
  efetch.fcgi?db=pubmed&retmode=xml (POST id=..)  full records, 200 per request

Searches use MeSH where the disease has a heading that names it (plan.py checks that the
heading matches the disease name, MeSH often maps rare diseases to a broader descriptor),
always OR-ed with the names in title/abstract so recent, not yet indexed papers are found:
  disease               "<heading>"[MeSH Major Topic] OR "<name>"[tiab] ...
  disease_subheading    "<heading>/<qualifier>"[MeSH]  (genetics, therapy, diagnosis, ...)
  disease_reviews       <disease> AND review[pt]
  disease+<kind>        <disease> AND <gene symbol / drug / phenotype names>[tiab]
  subtype               the subtype's own names / heading
  symptom_treatment     a typed symptom AND (treatment | therapy | drug)[tiab]
enrich() runs efetch for every PMID in the collection that lacks an abstract or MeSH
terms, so papers found by other providers (and those cited by the graph sources) get
title, abstract, journal, year, authors, MeSH, publication types, DOI and PMCID.
"""
import xml.etree.ElementTree as ET

from sources import _local

from . import ncbi
from .base import Collection, Concept, Paper, Provider, Query, gene_terms

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BATCH = 200


def _q(s: str) -> str:
    return '"' + s.replace('"', "") + '"'


def mesh_field(c: Concept, major: bool = False) -> str:
    """Supplementary concepts (C ids, most rare diseases) have their own field and no
    major-topic flag."""
    if (c.mesh_id or "").startswith("C"):
        return "Supplementary Concept"
    return "MeSH Major Topic" if major else "MeSH Terms"


def disease_term(c: Concept, major: bool = False) -> str:
    parts = [f"{_q(c.mesh)}[{mesh_field(c, major)}]"] if c.mesh and c.mesh_exact else []
    parts += [f"{_q(n)}[tiab]" for n in c.names[:6]]
    return "(" + " OR ".join(parts) + ")"


def other_term(c: Concept) -> str | None:
    names = gene_terms(c) if c.kind == "gene" else (c.names[:4] or [c.label])
    if not names:
        return None
    parts = [f"{_q(n)}[tiab]" for n in names]
    if c.mesh and c.mesh_exact:
        parts.append(f"{_q(c.mesh)}[{mesh_field(c)}]")
    return "(" + " OR ".join(parts) + ")"


class PubmedProvider(Provider):
    name = "pubmed"
    throttle = ncbi.EUTILS
    relations = frozenset({"disease", "disease_subheading", "disease_reviews", "disease+gene",
                           "disease+drug", "disease+phenotype", "subtype",
                           "symptom_treatment"})

    def term(self, q: Query) -> str | None:
        d = q.disease
        if q.relation == "symptom_treatment":  # q.disease is the typed phenotype
            return f"{disease_term(d)} AND (treatment[tiab] OR therapy[tiab] OR drug[tiab])"
        if q.relation == "disease":
            return disease_term(d, major=True)
        if q.relation == "disease_subheading":
            return f"{_q(d.mesh + '/' + q.qualifier)}[MeSH Terms]" if d.mesh and d.mesh_exact \
                else None
        if q.relation == "disease_reviews":
            return f"{disease_term(d)} AND review[pt]"
        if q.relation == "subtype":
            return disease_term(q.other)
        if q.other and other_term(q.other):
            return f"{disease_term(d)} AND {other_term(q.other)}"
        return None

    def search(self, q: Query) -> tuple[str, list[Paper]]:
        term = self.term(q)
        if not term:
            return "", []
        try:
            d = self.fetch_json(f"{EUTILS}/esearch.fcgi", ncbi.eutils_params(
                db="pubmed", term=term, retmax=q.cap, sort="relevance", retmode="json"))
            ids = d.get("esearchresult", {}).get("idlist") or []
        except Exception as e:
            self.fail(f"esearch {term[:80]}", e)
            return term, []
        return term, [Paper(pmid=i, hits=[self.hit(term, q, r)]) for r, i in enumerate(ids)]

    # -- efetch ----------------------------------------------------------------
    def enrich(self, coll: Collection):
        todo = [p.pmid for p in coll.papers if p.pmid and not (p.abstract and p.mesh)]
        done = 0
        for batch in _local_first(todo, BATCH):
            try:
                xml = self.fetch(f"{EUTILS}/efetch.fcgi", method="POST", data=ncbi.eutils_params(
                    db="pubmed", retmode="xml", id=",".join(batch)))
                for p in parse_efetch(xml):
                    coll.add(p)
            except Exception as e:
                self.fail(f"efetch {len(batch)} ids", e)
            done += len(batch)
            print(f"  pubmed efetch {done}/{len(todo)}", flush=True)


def _local_first(pmids: list[str], size: int) -> list[list[str]]:
    """Batches of PMIDs: those in the local PubMed subset first (answered locally as a
    whole), then the rest (sent to the API)."""
    local = _local.pubmed_has(pmids)
    groups = [[p for p in pmids if p in local], [p for p in pmids if p not in local]]
    return [g[i:i + size] for g in groups for i in range(0, len(g), size)]


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_efetch(xml: str) -> list[Paper]:
    out = []
    for art in ET.fromstring(xml).iter("PubmedArticle"):
        mc = art.find("MedlineCitation")
        a = mc.find("Article")
        abstract = []
        for t in a.findall("Abstract/AbstractText"):
            label = t.get("Label")
            abstract.append(f"{label}: {_text(t)}" if label else _text(t))
        year = None
        pd = a.find("Journal/JournalIssue/PubDate")
        if pd is not None:
            y = pd.findtext("Year") or (pd.findtext("MedlineDate") or "")[:4]
            year = int(y) if y.isdigit() else None
        authors = []
        for au in a.findall("AuthorList/Author"):
            name = " ".join(x for x in (au.findtext("LastName"), au.findtext("Initials")) if x)
            authors.append(name or au.findtext("CollectiveName") or "")
        mesh = []
        for h in mc.findall("MeshHeadingList/MeshHeading"):
            dn = h.find("DescriptorName")
            major = dn.get("MajorTopicYN") == "Y" or any(
                qn.get("MajorTopicYN") == "Y" for qn in h.findall("QualifierName"))
            quals = [_text(qn) for qn in h.findall("QualifierName")]
            mesh.append(("*" if major else "") + _text(dn) + "".join(f"/{x}" for x in quals))
        ids = {i.get("IdType"): _text(i) for i in art.findall("PubmedData/ArticleIdList/ArticleId")}
        out.append(Paper(
            pmid=mc.findtext("PMID"), pmcid=ids.get("pmc"), doi=ids.get("doi"),
            title=_text(a.find("ArticleTitle")) or None,
            abstract="\n".join(x for x in abstract if x) or None,
            journal=a.findtext("Journal/ISOAbbreviation") or a.findtext("Journal/Title"),
            year=year, authors=[x for x in authors if x], mesh=mesh,
            pub_types=[_text(x) for x in a.findall("PublicationTypeList/PublicationType")],
            keywords=[_text(k) for k in mc.findall("KeywordList/Keyword") if _text(k)]))
    return out
