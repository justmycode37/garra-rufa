"""Live queries against ClinicalTrials.gov, NIH RePORTER, and PubMed.

Each function searches one condition and returns a short, citable record.
Parsing is separate from HTTP so tests can run on saved JSON.
"""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .catalog import QUERY_SOURCES
from .client import USER_AGENT
from .envelope import envelope

MAX_BYTES = 4 * 1024 * 1024


def _request_json(url: str, payload: dict | None = None, timeout: int = 40) -> dict:
    data = None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("response exceeds 4 MB")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("expected a JSON object")
    return parsed


def _failed(source_id: str, exc: Exception) -> dict:
    source = QUERY_SOURCES[source_id]
    if isinstance(exc, HTTPError) and exc.code == 404:
        status = "not_found"
        message = "HTTP 404"
    elif isinstance(exc, (HTTPError, URLError, TimeoutError, OSError, ValueError)):
        status = "source_unavailable"
        message = str(getattr(exc, "reason", exc))
    else:
        status = "source_unavailable"
        message = "response could not be read"
    return envelope(
        status=status,
        source_id=source.id,
        publisher=source.publisher,
        license=source.license,
        url=source.url,
        message=message,
        records=[],
    )


def parse_clinicaltrials(payload: dict) -> list[dict]:
    records = []
    for study in payload.get("studies") or []:
        protocol = study.get("protocolSection") or {}
        identity = protocol.get("identificationModule") or {}
        status = protocol.get("statusModule") or {}
        conditions = (protocol.get("conditionsModule") or {}).get("conditions") or []
        design = protocol.get("designModule") or {}
        nct_id = identity.get("nctId") or ""
        if not nct_id:
            continue
        records.append(
            {
                "nct_id": nct_id,
                "title": identity.get("briefTitle") or "",
                "status": status.get("overallStatus") or "",
                "conditions": conditions,
                "study_type": design.get("studyType") or "",
                "phases": design.get("phases") or [],
                "url": f"https://clinicaltrials.gov/study/{nct_id}",
            }
        )
    return records


def search_clinicaltrials(condition: str, *, page_size: int = 5) -> dict:
    source = QUERY_SOURCES["clinicaltrials"]
    term = condition.strip()
    if not term:
        return envelope(
            status="rejected",
            source_id=source.id,
            publisher=source.publisher,
            license=source.license,
            url=source.url,
            message="condition is empty",
            records=[],
        )
    page_size = max(1, min(page_size, 20))
    query = urlencode(
        {
            "query.term": term,
            "pageSize": page_size,
            "fields": "NCTId,BriefTitle,OverallStatus,Condition,StudyType,Phase",
        }
    )
    url = f"{source.url}?{query}"
    try:
        payload = _request_json(url)
    except Exception as exc:
        return _failed(source.id, exc)
    records = parse_clinicaltrials(payload)
    status = "ok" if records else "not_found"
    return envelope(
        status=status,
        source_id=source.id,
        publisher=source.publisher,
        license=source.license,
        url=url,
        message="" if records else f"No studies matched {term!r}.",
        records=records,
        next_page_token=payload.get("nextPageToken") or "",
    )


def parse_reporter(payload: dict) -> list[dict]:
    records = []
    for row in payload.get("results") or []:
        agency = row.get("agency_ic_admin") or {}
        org = row.get("organization") or {}
        abstract = (row.get("abstract_text") or "").strip()
        records.append(
            {
                "project_num": row.get("project_num") or "",
                "title": row.get("project_title") or "",
                "pi": row.get("contact_pi_name") or "",
                "fiscal_year": row.get("fiscal_year"),
                "agency": agency.get("abbreviation") or agency.get("name") or "",
                "organization": org.get("org_name") or "",
                "url": row.get("project_detail_url") or "",
                "abstract_excerpt": abstract[:400],
            }
        )
    return records


def search_reporter(condition: str, *, limit: int = 5) -> dict:
    source = QUERY_SOURCES["nih_reporter"]
    term = condition.strip()
    if not term:
        return envelope(
            status="rejected",
            source_id=source.id,
            publisher=source.publisher,
            license=source.license,
            url=source.url,
            message="condition is empty",
            records=[],
        )
    limit = max(1, min(limit, 20))
    payload = {
        "criteria": {
            "advanced_text_search": {
                "operator": "and",
                "search_field": "all",
                "search_text": term,
            }
        },
        "offset": 0,
        "limit": limit,
        "include_fields": [
            "ProjectTitle",
            "ProjectNum",
            "ContactPiName",
            "FiscalYear",
            "AgencyIcAdmin",
            "Organization",
            "ProjectDetailUrl",
            "AbstractText",
        ],
    }
    try:
        body = _request_json(source.url, payload)
    except Exception as exc:
        return _failed(source.id, exc)
    records = parse_reporter(body)
    total = (body.get("meta") or {}).get("total")
    status = "ok" if records else "not_found"
    return envelope(
        status=status,
        source_id=source.id,
        publisher=source.publisher,
        license=source.license,
        url=source.url,
        message="" if records else f"No NIH projects matched {term!r}.",
        records=records,
        total=total,
    )


def parse_pubmed_summary(payload: dict) -> list[dict]:
    result = payload.get("result") or {}
    uids = result.get("uids") or []
    records = []
    for uid in uids:
        row = result.get(str(uid)) or {}
        if not isinstance(row, dict):
            continue
        authors = [author.get("name") for author in row.get("authors") or [] if author.get("name")]
        records.append(
            {
                "pmid": str(row.get("uid") or uid),
                "title": row.get("title") or "",
                "journal": row.get("fulljournalname") or row.get("source") or "",
                "pubdate": row.get("pubdate") or "",
                "authors": authors[:8],
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
            }
        )
    return records


def search_pubmed(condition: str, *, retmax: int = 5) -> dict:
    source = QUERY_SOURCES["pubmed"]
    term = condition.strip()
    if not term:
        return envelope(
            status="rejected",
            source_id=source.id,
            publisher=source.publisher,
            license=source.license,
            url=source.url,
            message="condition is empty",
            records=[],
        )
    retmax = max(1, min(retmax, 20))
    identity = {"tool": "garra-rufa"}
    email = os.environ.get("NCBI_EMAIL", "").strip()
    if email:
        identity["email"] = email
    search_url = source.url + "?" + urlencode(
        {"db": "pubmed", "retmode": "json", "retmax": retmax, "term": term, **identity}
    )
    try:
        found = _request_json(search_url)
        id_list = ((found.get("esearchresult") or {}).get("idlist")) or []
        count = (found.get("esearchresult") or {}).get("count")
        if not id_list:
            return envelope(
                status="not_found",
                source_id=source.id,
                publisher=source.publisher,
                license=source.license,
                url=search_url,
                message=f"No PubMed records matched {term!r}.",
                records=[],
                total=count,
            )
        summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urlencode(
            {"db": "pubmed", "retmode": "json", "id": ",".join(id_list), **identity}
        )
        summary = _request_json(summary_url)
    except Exception as exc:
        return _failed(source.id, exc)
    records = parse_pubmed_summary(summary)
    return envelope(
        status="ok" if records else "not_found",
        source_id=source.id,
        publisher=source.publisher,
        license=source.license,
        url=search_url,
        records=records,
        total=count,
    )
