"""Local copy of the Human Phenotype Ontology and its disease annotations (helper, not a Source).

Files (official HPO release, downloaded once into <repo>/data/hpo/, gitignored):
  hp.obo           ontology: names, synonyms, is_a parents, alt ids
  phenotype.hpoa   disease -> phenotype annotations (OMIM / ORPHA / DECIPHER) with frequency
Parsed once into hpo.pickle. Delete the folder to refresh. The purl redirects to GitHub
release assets; when github.com is unreachable the GitHub API asset route is tried.

Used for:
  - symptom search (main.py --symptoms): resolve free-text symptoms to HP terms and rank
    diseases by how well their annotations cover the symptoms (rank_diseases)
  - specificity of a phenotype (information content, ic): "Arachnodactyly" is informative,
    "Autosomal dominant inheritance" or "Failure to thrive" are not
  - frequency labels -> numbers (freq_value), also for JAX / Monarch frequency strings
  - the papers behind each disease-phenotype annotation (references(): PMIDs of the
    hpoa `reference` column), used as Edge.evidence by the hpo source

Scoring (rank_diseases): for each query symptom q, a disease gets the information content
of the most informative term it shares with q's ancestors (q itself, or one of q's
descendants, counts as a full match; a shared ancestor such as "Abnormality of the
lens" counts as a partial match), scaled by how frequent that phenotype is in the
disease. Rare, specific symptoms therefore weigh more than common ones.
"""
import csv
import math
import pickle
import re
import sys
import threading
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "hpo"
PICKLE = DATA_DIR / "hpo.pickle"
FILES = {"hp.obo": "https://purl.obolibrary.org/obo/hp.obo",
         "phenotype.hpoa": "https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa"}
GH_RELEASE = "https://api.github.com/repos/obophenotype/human-phenotype-ontology/releases/latest"
INHERITANCE = "HP:0000005"  # "Mode of inheritance": generic, never a useful symptom/hub
PHENOTYPE_ROOT = "HP:0000118"  # "Phenotypic abnormality"

FREQ_TERMS = {"HP:0040280": 1.0, "HP:0040281": 0.9, "HP:0040282": 0.55,
              "HP:0040283": 0.17, "HP:0040284": 0.02, "HP:0040285": 0.0}
FREQ_WORDS = {"obligate": 1.0, "very frequent": 0.9, "frequent": 0.55, "occasional": 0.17,
              "very rare": 0.02, "excluded": 0.0}
UNKNOWN_FREQ = 0.5
WORD = re.compile(r"[a-z0-9]+")
STOP = {"of", "the", "and", "with", "a", "an", "in", "to", "abnormal", "abnormality"}


def freq_value(f) -> float:
    """HPO frequency (HP:00402xx, "3/7", "45%", "Very frequent", "") -> 0..1."""
    f = (f or "").strip()
    if not f:
        return UNKNOWN_FREQ
    if f in FREQ_TERMS:
        return FREQ_TERMS[f]
    low = f.lower()
    for w, v in FREQ_WORDS.items():  # "very frequent" before "frequent"
        if low.startswith(w):
            return v
    m = re.match(r"^(\d+)\s*/\s*(\d+)$", f)
    if m and int(m[2]):
        return int(m[1]) / int(m[2])
    m = re.match(r"^([\d.]+)\s*%$", f)
    if m:
        return float(m[1]) / 100
    return UNKNOWN_FREQ


def _words(s: str) -> frozenset[str]:
    return frozenset(WORD.findall(s.lower())) - STOP


def _download(session: requests.Session, fname: str) -> Path:
    path = DATA_DIR / fname
    if path.exists():
        return path
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[hpo] downloading {fname}...", file=sys.stderr, flush=True)
    urls = [(FILES[fname], {})]
    try:  # fallback: the same release asset through the GitHub API
        rel = session.get(GH_RELEASE, timeout=30).json()
        urls += [(a["url"], {"Accept": "application/octet-stream"})
                 for a in rel.get("assets", []) if a["name"] == fname]
    except Exception:
        pass
    part = path.with_suffix(path.suffix + ".part")
    err = None
    for url, headers in urls:
        try:
            with session.get(url, headers=headers, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            part.replace(path)
            return path
        except Exception as e:
            err = e
    raise RuntimeError(f"could not download {fname}: {err}")


class HpoData:
    def __init__(self, d: dict):
        self.name: dict[str, str] = d["name"]
        self.parents: dict[str, tuple[str, ...]] = d["parents"]
        self.alt: dict[str, str] = d["alt"]
        self.exact: dict[str, str] = d["exact"]  # lower label / exact synonym -> id
        self.related: dict[str, str] = d["related"]  # other synonyms -> id
        self.ann: dict[str, dict[str, float]] = d["ann"]  # disease -> {hp: freq}
        self.dname: dict[str, str] = d["dname"]
        self.count: dict[str, int] = d["count"]  # term -> diseases annotated (propagated)
        # (disease, hp) -> PMIDs of the annotation
        self.refs: dict[tuple[str, str], tuple[str, ...]] = d["refs"]
        self.n = len(self.ann)
        self.children: dict[str, set[str]] = {}
        for c, ps in self.parents.items():
            for p in ps:
                self.children.setdefault(p, set()).add(c)
        self.by_term: dict[str, set[str]] = {}
        for dis, terms in self.ann.items():
            for t in terms:
                self.by_term.setdefault(t, set()).add(dis)
        self._anc: dict[str, frozenset[str]] = {}
        self._desc: dict[str, frozenset[str]] = {}
        self._label_words = None

    # -- ontology --------------------------------------------------------------
    def canonical(self, hp: str) -> str | None:
        hp = self.alt.get(hp, hp)
        return hp if hp in self.name else None

    def ancestors(self, hp: str) -> frozenset[str]:
        """hp and all its is_a ancestors."""
        if hp not in self._anc:
            out, todo = {hp}, [hp]
            while todo:
                for p in self.parents.get(todo.pop(), ()):
                    if p not in out:
                        out.add(p)
                        todo.append(p)
            self._anc[hp] = frozenset(out)
        return self._anc[hp]

    def descendants(self, hp: str) -> frozenset[str]:
        if hp not in self._desc:
            out, todo = {hp}, [hp]
            while todo:
                for c in self.children.get(todo.pop(), ()):
                    if c not in out:
                        out.add(c)
                        todo.append(c)
            self._desc[hp] = frozenset(out)
        return self._desc[hp]

    def references(self, disease: str, hp: str) -> tuple[str, ...]:
        """PMIDs cited for "disease has phenotype hp" (disease: OMIM:/ORPHA:/DECIPHER:)."""
        disease = disease.replace("ORPHANET:", "ORPHA:").replace("Orphanet:", "ORPHA:")
        return self.refs.get((disease, self.alt.get(hp, hp)), ())

    def is_inheritance(self, hp: str) -> bool:
        return INHERITANCE in self.ancestors(self.alt.get(hp, hp))

    def ic(self, hp: str) -> float:
        """Information content: -log(share of annotated diseases that have the term)."""
        c = self.count.get(self.alt.get(hp, hp), 0)
        return math.log(self.n / c) if c else math.log(self.n)

    def specificity(self, hp: str) -> float:
        """ic scaled to 0..1 (0 = every disease has it); inheritance modes are 0."""
        return 0.0 if self.is_inheritance(hp) else self.ic(hp) / math.log(self.n)

    # -- symptom text -> HP term ------------------------------------------------
    def resolve(self, text: str) -> tuple[str, str, str] | None:
        """(HP id, label, how) for a symptom given as HP CURIE or free text, else None.
        how: "id", "exact", "synonym" or "approximate" (all query words in the label)."""
        t = text.strip()
        if t.upper().startswith("HP:"):
            hp = self.canonical("HP:" + t.split(":", 1)[1].zfill(7))
            return (hp, self.name[hp], "id") if hp else None
        low = " ".join(t.lower().split())
        for cand in (low, low.rstrip("s")):
            if cand in self.exact:
                hp = self.exact[cand]
                return hp, self.name[hp], "exact"
        for cand in (low, low.rstrip("s")):
            if cand in self.related:
                hp = self.related[cand]
                return hp, self.name[hp], "synonym"
        q = _words(t)
        if not q:
            return None
        if self._label_words is None:
            self._label_words = [(hp, _words(s)) for s, hp in
                                 [*self.exact.items(), *self.related.items()]]
        pheno = self.descendants(PHENOTYPE_ROOT)
        best = None
        for hp, ws in self._label_words:
            if q <= ws and hp in pheno:
                # fewest extra words, then the most widely used term
                key = (len(ws - q), -self.count.get(hp, 0))
                if best is None or key < best[0]:
                    best = (key, hp)
        return (best[1], self.name[best[1]], "approximate") if best else None

    def suggest(self, text: str, n: int = 3) -> list[tuple[str, str, str]]:
        """Closest HP terms for text resolve() could not map, as (id, name, matched
        label/synonym), most query words covered first. Only offered as suggestions:
        partial overlaps are too often wrong to use automatically ("tired all the time"
        -> "feeling hopeless all the time")."""
        def stem(ws):
            return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in ws}
        q = stem(_words(text))
        if not q:
            return []
        pheno = self.descendants(PHENOTYPE_ROOT)
        scored = []
        for s, hp in [*self.exact.items(), *self.related.items()]:
            if hp not in pheno:
                continue
            ws = stem(_words(s))
            k = len(q & ws)
            if k:
                scored.append((-k / len(q), len(ws - q), -self.count.get(hp, 0), hp, s))
        out: dict[str, tuple[str, str, str]] = {}
        for *_, hp, s in sorted(scored):
            out.setdefault(hp, (hp, self.name[hp], s))
            if len(out) >= n:
                break
        return list(out.values())

    # -- disease ranking ----------------------------------------------------------
    def _propagated(self, dis: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for t, f in self.ann[dis].items():
            for a in self.ancestors(t):
                if f > out.get(a, -1):
                    out[a] = f
        return out

    def rank_diseases(self, hps: list[str], top: int = 20) -> list[dict]:
        """Diseases best explaining the symptoms, best first. Each result: id, name,
        score, xrefs (same-named entries from other databases), matches
        [(query hp, matched hp, full match?, freq)]."""
        hps = [h for h in dict.fromkeys(hps) if h in self.name]
        cands: set[str] = set()
        for q in hps:
            for t in self.descendants(q):
                cands |= self.by_term.get(t, set())
        scored = []
        for dis in cands:
            p = self._propagated(dis)
            score, matches = 0.0, []
            for q in hps:
                best = None
                for a in self.ancestors(q):
                    if a in p and not self.is_inheritance(a):
                        s = self.ic(a) * (0.6 + 0.4 * p[a])
                        if best is None or s > best[0]:
                            best = (s, a, a == q, p[a])
                if best and best[0] > 0:
                    score += best[0]
                    matches.append((q, best[1], best[2], best[3]))
            scored.append({"id": dis, "name": self.dname.get(dis, dis), "score": score,
                           "matches": matches, "xrefs": []})
        scored.sort(key=lambda r: (-r["score"], r["id"]))
        # OMIM and Orphanet often both annotate one disease: merge entries with equal names
        out: list[dict] = []
        by_name: dict[str, dict] = {}
        for r in scored:
            key = " ".join(sorted(_words(r["name"])))
            if key in by_name:
                by_name[key]["xrefs"].append(r["id"])
                continue
            by_name[key] = r
            out.append(r)
            if len(out) >= top:
                break
        return out


def _parse(session: requests.Session) -> dict:
    obo, hpoa = _download(session, "hp.obo"), _download(session, "phenotype.hpoa")
    print("[hpo] parsing ontology and annotations (one-time)...", file=sys.stderr, flush=True)
    name, parents, alt, exact, related = {}, {}, {}, {}, {}
    cur: dict | None = None

    def flush(t):
        if t and t.get("id", "").startswith("HP:") and not t.get("obsolete"):
            hp = t["id"]
            name[hp] = t["name"]
            parents[hp] = tuple(t["is_a"])
            for a in t["alt"]:
                alt[a] = hp
            exact.setdefault(t["name"].lower(), hp)
            for s, scope in t["syn"]:
                (exact if scope == "EXACT" else related).setdefault(s.lower(), hp)

    with open(obo, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("["):
                flush(cur)
                cur = {"is_a": [], "alt": [], "syn": []} if line == "[Term]" else None
            elif cur is not None and ": " in line:
                k, v = line.split(": ", 1)
                if k == "id":
                    cur["id"] = v
                elif k == "name":
                    cur["name"] = v
                elif k == "is_a":
                    cur["is_a"].append(v.split(" ", 1)[0])
                elif k == "alt_id":
                    cur["alt"].append(v)
                elif k == "is_obsolete" and v == "true":
                    cur["obsolete"] = True
                elif k == "synonym":
                    m = re.match(r'^"(.*)" (\w+)', v)
                    if m:
                        cur["syn"].append((m[1], m[2]))
        flush(cur)

    ann: dict[str, dict[str, float]] = {}
    dname: dict[str, str] = {}
    refs: dict[tuple[str, str], tuple[str, ...]] = {}
    with open(hpoa, encoding="utf-8") as f:
        rows = (line for line in f if not line.startswith("#"))
        for r in csv.DictReader(rows, delimiter="\t"):
            if r.get("aspect") != "P" or r.get("qualifier") == "NOT":
                continue
            hp = alt.get(r["hpo_id"], r["hpo_id"])
            if hp not in name:
                continue
            dis = r["database_id"].replace("ORPHANET:", "ORPHA:")
            dname.setdefault(dis, r["disease_name"])
            pm = [x for x in (r.get("reference") or "").split(";") if x.startswith("PMID:")]
            if pm:
                refs[(dis, hp)] = tuple(dict.fromkeys((*refs.get((dis, hp), ()), *pm)))
            fv = freq_value(r.get("frequency"))
            if fv <= 0:
                continue
            d = ann.setdefault(dis, {})
            d[hp] = max(d.get(hp, 0), fv)

    data = {"name": name, "parents": parents, "alt": alt, "exact": exact, "related": related,
            "ann": ann, "dname": dname, "count": {}, "refs": refs}
    tmp = HpoData(data)
    count: dict[str, int] = {}
    for dis in ann:
        for t in tmp._propagated(dis):
            count[t] = count.get(t, 0) + 1
    data["count"] = count
    return data


_lock = threading.Lock()
_data: HpoData | None = None
_failed = False


def load(session: requests.Session | None = None) -> HpoData | None:
    """The parsed HPO data, or None when it cannot be downloaded (callers degrade)."""
    global _data, _failed
    with _lock:
        if _data is not None or _failed:
            return _data
        try:
            d = None
            if PICKLE.exists():
                with open(PICKLE, "rb") as f:
                    d = pickle.load(f)
                if "refs" not in d:  # pickle from before references were kept: re-parse
                    d = None
            if d is not None:
                _data = HpoData(d)
            else:
                if session is None:
                    session = requests.Session()
                    session.headers["User-Agent"] = "garra-rufa-query-test/0.1"
                d = _parse(session)
                with open(PICKLE, "wb") as f:
                    pickle.dump(d, f, protocol=pickle.HIGHEST_PROTOCOL)
                _data = HpoData(d)
        except Exception as e:
            _failed = True
            print(f"[hpo] local HPO data unavailable: {e!r}", file=sys.stderr)
        return _data
