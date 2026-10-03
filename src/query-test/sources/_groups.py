"""Shared helpers for the "groups" sources: who works on a disease (patient organisations,
expert centres, networks, research projects/consortia, trial sponsors). Not a source.

  ERNS / ern_code()   the 24 European Reference Networks under one id each (ERN:<code>,
                      the networkCode of the EC ERN directory), so Orphanet, EURORDIS and
                      the EC directory all point at the same ERN node
  norm()              name normalisation for matching disease names between sites
  disease_names()     the names a disease node is known by: its label plus the Orphanet
                      preferred term of its ORPHA ids (most group sites use Orphanet names)
  web_xref()          WEB:<domain> for an organisation's website, the one identifier that
                      the same organisation carries on different sites
  Throttle            polite per-host request spacing
  CachedFile          one-time download into <repo>/data/<source>/ (gitignored)

Node kinds used by the groups sources: patient_organisation, expert_centre,
expert_network, ern, healthcare_provider, research_project, research_consortium,
research_study, registry, biobank, clinical_trial, organisation (sponsors/collaborators).
"""
import html
import json
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlparse

from .base import TIMEOUT, Node

DATA_ROOT = Path(__file__).resolve().parents[3] / "data"
ORPHACODE_API = "https://api.orphacode.org/EN/ClinicalEntity"

# EC ERN directory networkCode -> official short name
ERNS = {
    "BOND": "ERN BOND (Rare Bone Disorders)",
    "CRANIO": "ERN CRANIO (Craniofacial anomalies and ENT disorders)",
    "Endo-ERN": "Endo-ERN (Rare Endocrine Conditions)",
    "EpiCARE": "ERN EpiCARE (Rare and Complex Epilepsies)",
    "ERKNet": "ERKNet (Rare Kidney Diseases)",
    "ERN-EYE": "ERN-EYE (Rare Eye Diseases)",
    "ERN-LUNG": "ERN-LUNG (Rare Respiratory Diseases)",
    "ERN-RND": "ERN-RND (Rare Neurological Diseases)",
    "ERN-SKIN": "ERN-Skin (Rare and Undiagnosed Skin Disorders)",
    "ERNICA": "ERNICA (Rare Inherited and Congenital Digestive Anomalies)",
    "EURACAN": "EURACAN (Rare Adult Solid Cancers)",
    "EURO-NMD": "EURO-NMD (Rare Neuromuscular Diseases)",
    "EuroBloodNet": "EuroBloodNet (Rare Haematological Diseases)",
    "eUROGEN": "eUROGEN (Rare Urogenital Diseases)",
    "GENTURIS": "ERN GENTURIS (Genetic Tumour Risk Syndromes)",
    "GUARD-HEART": "ERN GUARD-HEART (Rare Heart Diseases)",
    "ITHACA": "ERN ITHACA (Congenital Malformations and Rare Intellectual Disability)",
    "MetabERN": "MetabERN (Rare Hereditary Metabolic Disorders)",
    "PaedCAN": "ERN PaedCan (Paediatric Cancer)",
    "RARE-LIVER": "ERN RARE-LIVER (Rare Hepatological Diseases)",
    "ReCONNET": "ERN ReCONNET (Rare Connective Tissue and Musculoskeletal Diseases)",
    "RITA": "ERN RITA (Rare Immunodeficiency, Autoinflammatory and Autoimmune Diseases)",
    "TRANSPLANTCHILD": "ERN TransplantChild (Transplantation in Children)",
    "VASCERN": "VASCERN (Rare Multisystemic Vascular Diseases)",
}
# token (or two adjacent tokens joined) -> code; distinctive names first, then the short
# words that only identify an ERN in a text already known to name one ("ERN Skin")
_ERN_KEYS = [
    ("transplantchild", "TRANSPLANTCHILD"), ("eurobloodnet", "EuroBloodNet"),
    ("guardheart", "GUARD-HEART"), ("rareliver", "RARE-LIVER"), ("euronmd", "EURO-NMD"),
    ("genturis", "GENTURIS"), ("reconnet", "ReCONNET"), ("metabern", "MetabERN"),
    ("euracan", "EURACAN"), ("eurogen", "eUROGEN"), ("epicare", "EpiCARE"),
    ("endoern", "Endo-ERN"), ("vascern", "VASCERN"), ("paedcan", "PaedCAN"),
    ("ithaca", "ITHACA"), ("erknet", "ERKNet"), ("ernica", "ERNICA"), ("cranio", "CRANIO"),
    ("bond", "BOND"), ("rita", "RITA"), ("rnd", "ERN-RND"), ("lung", "ERN-LUNG"),
    ("skin", "ERN-SKIN"), ("eye", "ERN-EYE"),
]
_TOKEN = re.compile(r"[a-z0-9]+")
# hosts that many unrelated organisations share: no identity
_SHARED_HOSTS = {"facebook.com", "m.facebook.com", "instagram.com", "linkedin.com",
                 "twitter.com", "x.com", "youtube.com", "google.com", "sites.google.com",
                 "groups.google.com", "yahoo.com", "gmail.com", "wix.com", "linktr.ee"}


def ern_code(text: str) -> str | None:
    """The ERN a text names ("ERN LUNG (Pulmonary)", "VASCERN: European Reference
    Network on ..."), or None. Only call it on text known to name an ERN."""
    toks = _TOKEN.findall(html.unescape(text or "").lower())
    keys = set(toks) | {a + b for a, b in zip(toks, toks[1:])}
    return next((code for key, code in _ERN_KEYS if key in keys), None)


def ern_node(code: str, source: str) -> Node:
    return Node(ERNS.get(code, code), f"ERN:{code}", "ern", source)


def norm(s: str) -> str:
    """Lower case, accents and punctuation folded: "Graves' Disease" -> "graves disease"."""
    import unicodedata
    s = unicodedata.normalize("NFKD", html.unescape(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"['’`]s\b", "s", s)
    return " ".join(_TOKEN.findall(s))


def name_variants(name: str) -> list[str]:
    """Normalised keys for a site's disease name: the whole name, each part of an
    "A / B" name, and each without a trailing abbreviation in parentheses
    ("Brittle bone disorders (BBD) / osteogenesis imperfecta (OI)" ->
    ..., "osteogenesis imperfecta")."""
    parts = [name] + [p for p in re.split(r"\s+/\s+|;", name) if p.strip()]
    keys = []
    for p in parts:
        for v in (p, re.sub(r"\s*\([^()]*\)\s*$", "", p)):
            k = norm(v)
            if k and k not in keys:
                keys.append(k)
    return keys


def web_xref(url: str | None) -> str | None:
    """WEB:<host> for a website URL ("https://www.marfan.ch/de" -> "WEB:marfan.ch")."""
    if not url or not url.strip():
        return None
    u = url.strip()
    if "://" not in u:
        u = "http://" + u
    host = (urlparse(u).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host or host in _SHARED_HOSTS:
        return None
    return f"WEB:{host}"


def org_node(label: str, cid: str, kind: str, source: str, website: str | None = None,
             xrefs: tuple[str, ...] = ()) -> Node:
    w = web_xref(website)
    url = website.strip() if website and website.strip().startswith("http") else None
    return Node(html.unescape(label).strip(), cid, kind, source,
                tuple(dict.fromkeys((*xrefs, *([w] if w else [])))), {"url": url} if url else {})


def slug(s: str) -> str:
    return "-".join(_TOKEN.findall(norm(s)))[:80]


class Throttle:
    """Minimum spacing between requests to one host (shared by all users of the object)."""

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


# -- disease names -----------------------------------------------------------
_orpha_names: dict[str, str | None] = {}
_orpha_lock = threading.Lock()


def orphanet_name(session, code: str) -> str | None:
    """Orphanet preferred term of ORPHA:<code> (cached; None if unknown)."""
    with _orpha_lock:
        if code in _orpha_names:
            return _orpha_names[code]
    name = None
    try:
        r = session.get(f"{ORPHACODE_API}/orphacode/{quote(code)}/Name",
                        headers={"apiKey": "garra-rufa"}, timeout=TIMEOUT)
        if r.status_code == 200:
            name = (r.json() or {}).get("Preferred term")
    except Exception:
        pass
    with _orpha_lock:
        _orpha_names[code] = name
    return name


def orpha_codes(node: Node) -> list[str]:
    return [c.split(":", 1)[1] for c in (node.id, *node.xrefs)
            if c and c.split(":", 1)[0].upper() in ("ORPHA", "ORPHANET")]


def disease_names(node: Node, session, max_orpha: int = 2) -> list[str]:
    """Normalised names to match the node against a site's disease names: the node label
    and the Orphanet preferred terms of its (first few) ORPHA ids."""
    names = [node.label]
    for code in orpha_codes(node)[:max_orpha]:
        n = orphanet_name(session, code)
        if n:
            names.append(n)
    out = []
    for n in names:
        k = norm(n)
        if k and k not in out:
            out.append(k)
    return out


def is_disease(node: Node) -> bool:
    return node.kind in ("disease", "unknown", "term")


# -- one-time downloads --------------------------------------------------------
class CachedFile:
    """A file fetched once into data/<subdir>/<name> and reused; delete it to refresh.
    `fetch` returns the text to store. Failures are remembered for the run."""

    def __init__(self, subdir: str, name: str, fetch):
        self.path = DATA_ROOT / subdir / name
        self._fetch = fetch
        self._lock = threading.Lock()
        self._failed = False

    def text(self) -> str:
        with self._lock:
            if self.path.exists():
                return self.path.read_text(encoding="utf-8")
            if self._failed:
                raise RuntimeError(f"{self.path.name} unavailable")
            try:
                print(f"downloading {self.path.relative_to(DATA_ROOT)} ...", file=sys.stderr)
                data = self._fetch()
            except Exception as e:
                self._failed = True
                print(f"  download failed: {e}", file=sys.stderr)
                raise
            self.path.parent.mkdir(parents=True, exist_ok=True)
            part = self.path.with_suffix(self.path.suffix + ".part")
            part.write_text(data, encoding="utf-8")
            part.replace(self.path)
            return data

    def json(self):
        return json.loads(self.text())


def interleave(groups: list[list], limit: int) -> list:
    """Round-robin over groups so one large group cannot crowd out the others."""
    out: list = []
    i = 0
    while len(out) < limit and any(i < len(g) for g in groups):
        out += [g[i] for g in groups if i < len(g)][: limit - len(out)]
        i += 1
    return out
