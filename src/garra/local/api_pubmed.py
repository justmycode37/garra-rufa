"""NCBI E-utilities for db=pubmed (esearch / efetch XML / esummary JSON) from the local
rare-disease subset (pubmed.sqlite). esearch answers queries made of [tiab], [MeSH Terms],
[MeSH Major Topic], [Supplementary Concept] and [pt] terms (MeSH terms explode to
narrower headings); efetch / esummary answer only when every requested PMID is local."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from garra.local import pubmed as P
from garra.local.router import Reply, Req, register

HOST = "eutils.ncbi.nlm.nih.gov"


def _article(pmid: str, d: dict) -> ET.Element:
    art = ET.Element("PubmedArticle")
    mc = ET.SubElement(art, "MedlineCitation", Status="MEDLINE", Owner="NLM")
    ET.SubElement(mc, "PMID", Version="1").text = str(pmid)
    a = ET.SubElement(mc, "Article", PubModel="Print")
    j = ET.SubElement(a, "Journal")
    ji = ET.SubElement(j, "JournalIssue")
    pd = ET.SubElement(ji, "PubDate")
    if d.get("y"):
        ET.SubElement(pd, "Year").text = str(d["y"])
    elif d.get("pd"):
        ET.SubElement(pd, "MedlineDate").text = d["pd"]
    if d.get("jt"):
        ET.SubElement(j, "Title").text = d["jt"]
    if d.get("j"):
        ET.SubElement(j, "ISOAbbreviation").text = d["j"]
    ET.SubElement(a, "ArticleTitle").text = d.get("t") or ""
    if d.get("a"):
        ab = ET.SubElement(a, "Abstract")
        for label, text in d["a"]:
            el = ET.SubElement(ab, "AbstractText", **({"Label": label} if label else {}))
            el.text = text
    if d.get("au"):
        al = ET.SubElement(a, "AuthorList")
        for name in d["au"]:
            au = ET.SubElement(al, "Author")
            last, _, init = name.rpartition(" ")
            if last and init.isupper() and len(init) <= 4:
                ET.SubElement(au, "LastName").text = last
                ET.SubElement(au, "Initials").text = init
            else:
                ET.SubElement(au, "CollectiveName").text = name
    ptl = ET.SubElement(a, "PublicationTypeList")
    for pt in d.get("pt") or []:
        ET.SubElement(ptl, "PublicationType").text = pt
    if d.get("s"):
        sl = ET.SubElement(mc, "SupplMeshList")
        for s in d["s"]:
            ET.SubElement(sl, "SupplMeshName", Type="Disease").text = s
    if d.get("m"):
        ml = ET.SubElement(mc, "MeshHeadingList")
        for m in d["m"]:
            major = m.startswith("*")
            head, *quals = m.lstrip("*").split("/")
            mh = ET.SubElement(ml, "MeshHeading")
            ET.SubElement(mh, "DescriptorName",
                          MajorTopicYN="Y" if major and not quals else "N").text = head
            for q in quals:
                ET.SubElement(mh, "QualifierName", MajorTopicYN="Y" if major else "N").text = q
    if d.get("kw"):
        kl = ET.SubElement(mc, "KeywordList", Owner="NOTNLM")
        for k in d["kw"]:
            ET.SubElement(kl, "Keyword", MajorTopicYN="N").text = k
    pdata = ET.SubElement(art, "PubmedData")
    ids = ET.SubElement(pdata, "ArticleIdList")
    ET.SubElement(ids, "ArticleId", IdType="pubmed").text = str(pmid)
    if d.get("doi"):
        ET.SubElement(ids, "ArticleId", IdType="doi").text = d["doi"]
    if d.get("pmc"):
        ET.SubElement(ids, "ArticleId", IdType="pmc").text = d["pmc"]
    return art


def _records(ids: list[str]) -> dict[str, dict] | None:
    out = {}
    for i in ids:
        d = P.record(i) if i.isdigit() else None
        if d is None:
            return None  # all or nothing: the caller then asks the API for the batch
        out[i] = d
    return out


@register(HOST, "/entrez/eutils/", "pubmed")
def eutils(req: Req) -> Reply | None:
    if (req.get("db") or "").lower() != "pubmed":
        return None
    util = req.path.rsplit("/", 1)[-1].removesuffix(".fcgi")
    if util == "esearch":
        if req.get("usehistory") or req.get("mindate") or req.get("datetype"):
            return None
        n = int(req.get("retmax") or 20)
        start = int(req.get("retstart") or 0)
        sort = (req.get("sort") or "").lower()
        try:
            count, ids = P.search(req.get("term") or "", n + start,
                                  "relevance" if sort in ("relevance", "") else "date")
        except P.Unsupported:
            return None
        ids = ids[start:]
        if (req.get("retmode") or "xml").lower() != "json":
            body = ("<?xml version=\"1.0\" ?><eSearchResult><Count>%d</Count><RetMax>%d"
                    "</RetMax><RetStart>%d</RetStart><IdList>%s</IdList></eSearchResult>"
                    % (count, len(ids), start, "".join(f"<Id>{i}</Id>" for i in ids)))
            return Reply(body, content_type="text/xml")
        return Reply({"header": {"type": "esearch", "version": "0.3"}, "esearchresult": {
            "count": str(count), "retmax": str(len(ids)), "retstart": str(start),
            "idlist": ids, "translationset": [], "querytranslation": req.get("term")}})
    ids = req.getall("id")
    if not ids:
        return None
    recs = _records(ids)
    if recs is None:
        return None
    if util == "efetch":
        if (req.get("retmode") or "xml").lower() != "xml" or \
                (req.get("rettype") or "xml").lower() not in ("xml", "full"):
            return None
        root = ET.Element("PubmedArticleSet")
        for i, d in recs.items():
            root.append(_article(i, d))
        body = '<?xml version="1.0" ?>\n' + ET.tostring(root, encoding="unicode")
        return Reply(body, content_type="text/xml")
    if util == "esummary":
        res: dict = {"uids": list(recs)}
        for i, d in recs.items():
            res[i] = {"uid": i, "title": d.get("t") or "", "source": d.get("j") or "",
                      "fulljournalname": d.get("jt") or d.get("j") or "",
                      "pubdate": d.get("pd") or str(d.get("y") or ""),
                      "authors": [{"name": a, "authtype": "Author"} for a in d.get("au") or []],
                      "pubtype": d.get("pt") or [],
                      "articleids": [{"idtype": "pubmed", "value": i}]
                      + ([{"idtype": "doi", "value": d["doi"]}] if d.get("doi") else [])
                      + ([{"idtype": "pmc", "value": d["pmc"]}] if d.get("pmc") else [])}
        return Reply({"header": {"type": "esummary", "version": "0.3"}, "result": res})
    return None
