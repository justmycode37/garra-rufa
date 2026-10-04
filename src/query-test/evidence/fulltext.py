"""Full text of a paper, split into numbered passages the LLM cites as evidence.

  1. Europe PMC  /{PMCID}/fullTextXML                       JATS XML (open-access subset)
  2. PMC BioC    bionlp/RESTful/pmcoa.cgi/BioC_json/{PMCID}/unicode   (PMC OA, NCBI)
  3. otherwise the title + abstract from the papers JSON ("abstract")

Passages are paragraphs, figure / table captions and list items with ids P1, P2, ...
and the (top-level) section they belong to; references, acknowledgements, funding,
competing interests and table bodies are dropped. Over --max-chars, Methods passages go
first, then passages from the end. Responses are cached like every other request.
"""
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from literature.base import Provider, Throttle, norm_pmcid

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/{}/fullTextXML"
BIOC = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{}/unicode"
SKIP_SEC = re.compile(r"reference|acknowledg|funding|conflict|competing|disclosure|"
                      r"author.?contribution|abbreviation|supplementary|data availability",
                      re.I)
BIOC_KEEP = {"TITLE", "ABSTRACT", "INTRO", "METHODS", "RESULTS", "DISCUSS", "CONCL", "CASE",
             "FIG", "TABLE"}


@dataclass
class Passage:
    id: str
    section: str
    text: str


@dataclass
class Doc:
    source: str  # europepmc | pmc_bioc | abstract
    passages: list[Passage] = field(default_factory=list)
    truncated: bool = False

    def by_id(self) -> dict[str, Passage]:
        return {p.id: p for p in self.passages}

    def render(self) -> str:
        return "\n\n".join(f"[{p.id} | {p.section}] {p.text}" for p in self.passages)

    def chars(self) -> int:
        return sum(len(p.text) for p in self.passages)


def clean(s: str) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def parse_jats(xml: str) -> list[tuple[str, str]]:
    """(section, text) pairs of a JATS article."""
    root = ET.fromstring(xml)
    out: list[tuple[str, str]] = []
    title = root.find(".//article-meta/title-group/article-title")
    if title is not None:
        out.append(("Title", _text(title)))
    for ab in root.findall(".//article-meta/abstract"):
        if ab.get("abstract-type") in ("graphical", "teaser"):
            continue
        for p in ab.iter("p"):
            out.append(("Abstract", _text(p)))

    def walk(el, section: str):
        for ch in el:
            t = _tag(ch)
            if t == "sec":
                head = ch.find("title")
                name = section or (_text(head) if head is not None else "Body")
                if head is not None and SKIP_SEC.search(_text(head)):
                    continue
                walk(ch, name)
            elif t == "p":
                # inline figures / tables inside a paragraph: caption separately
                for sub in list(ch):
                    if _tag(sub) in ("fig", "table-wrap"):
                        cap = sub.find("caption")
                        if cap is not None:
                            out.append((section or "Body", _text(cap)))
                        ch.remove(sub)
                out.append((section or "Body", _text(ch)))
            elif t in ("fig", "table-wrap"):
                cap = ch.find("caption")
                if cap is not None:
                    label = ch.find("label")
                    lab = (_text(label) + ". ") if label is not None else ""
                    out.append((section or "Body", lab + _text(cap)))
            elif t in ("list", "boxed-text", "disp-quote"):
                out.append((section or "Body", _text(ch)))
    body = root.find(".//body")
    if body is not None:
        walk(body, "")
    return [(s, t) for s, t in out if len(t) > 20]


def parse_bioc(d) -> list[tuple[str, str]]:
    d = d[0] if isinstance(d, list) else d
    docs = (d or {}).get("documents") or []
    out = []
    section = ""
    for p in docs[0].get("passages") if docs else []:
        inf = p.get("infons") or {}
        st, typ, text = inf.get("section_type", ""), inf.get("type", ""), clean(p.get("text"))
        if st not in BIOC_KEEP or typ in ("table", "ref") or not text:
            continue
        if typ.startswith("title") or typ.startswith("abstract_title"):
            section = text if typ in ("title_1", "abstract_title_1") else section
            continue
        name = {"TITLE": "Title", "ABSTRACT": "Abstract"}.get(st) or section or st.title()
        if len(text) > 20:
            out.append((name, text))
    return out


def number(pairs: list[tuple[str, str]], max_chars: int) -> tuple[list[Passage], bool]:
    ps = [Passage(f"P{i + 1}", s[:60], t) for i, (s, t) in enumerate(pairs)]
    total = sum(len(p.text) for p in ps)
    if total <= max_chars:
        return ps, False
    drop = set()
    for p in ps:  # Methods first
        if total <= max_chars:
            break
        if re.search(r"method|material|patients and|statistic", p.section, re.I):
            drop.add(p.id)
            total -= len(p.text)
    for p in reversed(ps):  # then from the end
        if total <= max_chars:
            break
        if p.id not in drop and p.section not in ("Title", "Abstract"):
            drop.add(p.id)
            total -= len(p.text)
    return [p for p in ps if p.id not in drop], True


class FullTextProvider(Provider):
    name = "fulltext"
    throttle = Throttle(0.35)

    def get(self, paper: dict, max_chars: int = 150_000) -> Doc:
        pmcid = norm_pmcid(paper.get("pmcid"))
        if pmcid:
            for source, url, parse in (("europepmc", EPMC, parse_jats),
                                       ("pmc_bioc", BIOC, lambda t: parse_bioc(json.loads(t)))):
                try:
                    pairs = parse(self.fetch(url.format(pmcid)))
                except Exception as e:
                    if "404" not in str(e):
                        self.fail(f"{source} {pmcid}", e)
                    continue
                # a body, not just title + abstract
                if sum(s not in ("Title", "Abstract") for s, _ in pairs) >= 3:
                    ps, cut = number(pairs, max_chars)
                    return Doc(source, ps, cut)
        pairs = [("Title", clean(paper.get("title")))]
        parts = re.split(r"\n{2,}|<h4>", paper.get("abstract") or "")
        pairs += [("Abstract", t) for x in parts if (t := clean(x))]
        ps, cut = number([p for p in pairs if p[1]], max_chars)
        return Doc("abstract", ps, cut)
