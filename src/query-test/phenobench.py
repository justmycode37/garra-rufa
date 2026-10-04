#!/usr/bin/env python3
"""Benchmark the first graph (main.py) against solved patients from the GA4GH
phenopacket-store (https://github.com/monarch-initiative/phenopacket-store).

Every phenopacket is one published patient: the HPO terms observed and explicitly
excluded, the diagnosis (an OMIM disease) and the causative variant(s) with their gene.
That gives ground truth for the two things the first graph is used for:

  rank     the candidate search of main.py (resolve.rank, sources/_hpoa.py): give a
           patient's observed terms (and/or the causal gene) as the input and see where
           the diagnosis lands. top-1 / top-3 / top-10 and MRR, per patient (micro) and
           averaged per disease (macro: a few large cohorts, e.g. LMNA, do not dominate).
           Also: how often the top candidate is contradicted by a term the patient was
           examined for and does not have ("excluded"); the ranking ignores excluded terms,
           so "rescuable" counts the misses where the top-1 is contradicted and the
           diagnosis is not, i.e. what an excluded-aware score could win.
  profile  a graph JSON written by main.py (-o run.json): the focus disease's profile as
           present.py shows it, compared with the patients of that disease: which share of
           the features the patients have the profile mentions (patient-weighted;
           ontology-aware), which common features it misses, which "very frequent" profile
           symptoms the patients mostly lack, whether the causal genes are listed, and
           which patient variants the graph surfaced (hgvs.g vs the ClinVar variant nodes).

Leakage: HPO's phenotype.hpoa now includes annotations computed from phenopacket-store
(frequencies such as 7/9 citing the cohort's PMIDs), so a patient partly describes the
disease it is tested against. rank therefore runs leave-publication-out by default: while
a patient is ranked, the annotations of its diagnosis whose only reference is the
patient's own publication are removed (--no-holdout: off). Annotations aggregated over
several publications stay, so the remaining leak is small but not zero. profile reads a
finished graph and cannot hold out; read its recall as an upper bound.

Perturbations (rank, seeded per patient): --max-terms k random observed terms,
--imprecision p replaces each term by one of its parents with probability p, --noise n
adds n random annotated phenotype terms. --improvements works as on main.py.

Usage:
  python src/query-test/phenobench.py download
  python src/query-test/phenobench.py rank --per-disease 2 --max-cases 200
  python src/query-test/phenobench.py rank --input hpo+gene --json runs/bench/rank.json
  python src/query-test/phenobench.py rank --max-terms 5 --noise 2 --imprecision 0.3
  python src/query-test/phenobench.py rank --cohort FBN1 --cohort MECP2 --per-disease 50
  python src/query-test/phenobench.py profile runs/marfan.json runs/study/*.json
"""
import argparse
import json
import random
import statistics
import sys
import zipfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import improvements  # noqa: E402
import resolve  # noqa: E402
from resolve import Interpretation, Part  # noqa: E402
from sources import _hpoa  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "phenopackets"
ZIP = DATA_DIR / "all_phenopackets.zip"
RELEASE = ("https://github.com/monarch-initiative/phenopacket-store/releases/latest/"
           "download/all_phenopackets.zip")
INPUTS = ("hpo", "hpo+gene", "gene")
FREQUENT = 0.8  # "very frequent" and above: a disease that has it contradicts its absence


@dataclass(frozen=True)
class Case:
    id: str
    cohort: str  # the phenopacket-store folder (usually the gene)
    pmid: str | None
    disease_id: str
    disease_label: str
    observed: tuple[str, ...]
    excluded: tuple[str, ...]
    genes: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()  # hgvs.g expressions of the causative variants


def parse(packet: dict, cohort: str = "") -> list[Case]:
    """One Case per diagnosed disease of a phenopacket (almost always one)."""
    obs, exc = [], []
    for f in packet.get("phenotypicFeatures") or ():
        hp = (f.get("type") or {}).get("id", "")
        if hp.startswith("HP:"):
            (exc if f.get("excluded") else obs).append(hp)
    refs = [r.get("id", "") for r in (packet.get("metaData") or {}).get("externalReferences")
            or () if r.get("id", "").startswith("PMID:")]
    pmid = refs[0] if refs else None
    out = []
    for i in packet.get("interpretations") or ():
        dis = (i.get("diagnosis") or {}).get("disease") or {}
        if not dis.get("id"):
            continue
        genes, variants = [], []
        for gi in (i.get("diagnosis") or {}).get("genomicInterpretations") or ():
            vd = (gi.get("variantInterpretation") or {}).get("variationDescriptor") or {}
            sym = (vd.get("geneContext") or {}).get("symbol") or \
                (gi.get("gene") or {}).get("symbol")
            if sym:
                genes.append(sym.upper())
            variants += [e["value"] for e in vd.get("expressions") or ()
                         if e.get("syntax") == "hgvs.g" and e.get("value")]
        out.append(Case(packet.get("id", ""), cohort, pmid, dis["id"], dis.get("label", ""),
                        tuple(dict.fromkeys(obs)), tuple(dict.fromkeys(exc)),
                        tuple(dict.fromkeys(genes)), tuple(dict.fromkeys(variants))))
    if not out and packet.get("diseases"):  # no interpretation: the diseases block
        d = packet["diseases"][0].get("term") or {}
        if d.get("id"):
            out.append(Case(packet.get("id", ""), cohort, pmid, d["id"], d.get("label", ""),
                            tuple(dict.fromkeys(obs)), tuple(dict.fromkeys(exc))))
    return out


def download(path: Path = ZIP) -> Path:
    import requests
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {RELEASE} ...", file=sys.stderr)
    with requests.get(RELEASE, stream=True, timeout=300) as r:
        r.raise_for_status()
        part = path.with_suffix(".part")
        with open(part, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    part.replace(path)
    return path


def load_cases(path: Path = ZIP, cohorts: list[str] | None = None) -> list[Case]:
    """All cases of the release zip (or a folder of phenopacket JSON files)."""
    want = {c.upper() for c in cohorts or ()}
    out: list[Case] = []

    def keep(cohort):
        return not want or cohort.upper() in want
    if path.is_dir():
        for f in sorted(path.rglob("*.json")):
            if keep(f.parent.name):
                out += parse(json.loads(f.read_text(encoding="utf-8")), f.parent.name)
        return out
    with zipfile.ZipFile(path) as z:
        for name in sorted(z.namelist()):
            if not name.endswith(".json"):
                continue
            parts = name.split("/")
            cohort = parts[-2] if len(parts) >= 2 else ""
            if keep(cohort):
                out += parse(json.loads(z.read(name)), cohort)
    return out


def sample(cases: list[Case], per_disease: int | None, max_cases: int | None,
           seed: int = 0) -> list[Case]:
    """Deterministic: up to per_disease random cases of every disease, then up to
    max_cases of them (whole diseases dropped at random, so macro averages stay fair)."""
    by: dict[str, list[Case]] = defaultdict(list)
    for c in cases:
        if c.observed:
            by[c.disease_id].append(c)
    rng = random.Random(seed)
    groups = []
    for d in sorted(by):
        cs = sorted(by[d], key=lambda c: c.id)
        if per_disease and len(cs) > per_disease:
            cs = rng.sample(cs, per_disease)
        groups.append(cs)
    rng.shuffle(groups)
    out: list[Case] = []
    for cs in groups:
        if max_cases and len(out) + len(cs) > max_cases:
            if out:
                continue
            cs = cs[:max_cases]
        out += cs
    return sorted(out, key=lambda c: (c.disease_id, c.id))


# -- identity --------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _keys(cid: str, xrefs: tuple[str, ...], name: str) -> frozenset[str]:
    return frozenset(_identity_keys({"id": cid, "xrefs": list(xrefs), "name": name}))


_identity_keys = resolve.identity_keys


def keys_of(r: dict) -> frozenset[str]:
    """resolve.identity_keys, memoised (it queries the local indexes per call)."""
    return _keys(r["id"], tuple(r.get("xrefs") or ()), r.get("name") or "")


@contextmanager
def fast_identity():
    """Make resolve.dedupe_ranking use the memoised identity keys."""
    resolve.identity_keys = keys_of
    try:
        yield
    finally:
        resolve.identity_keys = _identity_keys


def truth_keys(case: Case) -> frozenset[str]:
    return keys_of({"id": case.disease_id, "name": case.disease_label})


def find_rank(ranking: list[dict], truth: frozenset[str]) -> int | None:
    """1-based rank of the first entry that is the diagnosis (shared OMIM / ORPHA / MONDO
    id or folded name), else None."""
    for i, r in enumerate(ranking, 1):
        if keys_of(r) & truth:
            return i
    return None


# -- holdout and perturbation ------------------------------------------------------------
@contextmanager
def holdout(hpo, case: Case, enabled: bool = True):
    """Leave-publication-out: hide the annotations of the case's diagnosis whose only
    references are the case's own publication. Yields how many were hidden."""
    removed: list[tuple[str, str, float]] = []
    if enabled and case.pmid:
        for d in sorted(i for i in truth_keys(case) if i in hpo.ann):
            for hp, f in list(hpo.ann[d].items()):
                refs = hpo.refs.get((d, hp))
                if refs and set(refs) <= {case.pmid}:
                    removed.append((d, hp, f))
                    del hpo.ann[d][hp]
                    hpo.by_term.get(hp, set()).discard(d)
    try:
        yield len(removed)
    finally:
        for d, hp, f in removed:
            hpo.ann[d][hp] = f
            hpo.by_term.setdefault(hp, set()).add(d)


@lru_cache(maxsize=4)
def _noise_pool(hpo_id: int, hpo) -> tuple[str, ...]:
    pheno = hpo.descendants(_hpoa.PHENOTYPE_ROOT)
    return tuple(sorted(t for t in hpo.by_term if t in pheno and t != _hpoa.PHENOTYPE_ROOT))


def perturb(case: Case, hpo, max_terms: int | None = None, imprecision: float = 0.0,
            noise: int = 0, seed: int = 0) -> list[str]:
    """The observed terms the ranking is given (deterministic per case and seed)."""
    rng = random.Random(f"{seed}:{case.id}:{case.disease_id}")
    terms = [hpo.canonical(t) for t in case.observed]
    terms = [t for t in terms if t and t in hpo.descendants(_hpoa.PHENOTYPE_ROOT)]
    if max_terms and len(terms) > max_terms:
        terms = rng.sample(terms, max_terms)
    if imprecision:
        out = []
        for t in terms:
            parents = [p for p in hpo.parents.get(t, ()) if p != _hpoa.PHENOTYPE_ROOT]
            out.append(rng.choice(sorted(parents)) if parents and rng.random() < imprecision
                       else t)
        terms = out
    if noise:
        pool = _noise_pool(id(hpo), hpo)
        terms += rng.sample(pool, noise)
    return list(dict.fromkeys(terms))


# -- excluded phenotypes ------------------------------------------------------------------
def contradicted(hpo, disease_ids, excluded) -> list[str]:
    """Excluded terms of the patient that the disease has (itself or a more specific
    term) at FREQUENT or more: the patient was examined for it and does not have it."""
    ann: dict[str, float] = {}
    for d in disease_ids:
        for hp, f in hpo.ann.get(d, {}).items():
            ann[hp] = max(ann.get(hp, 0.0), f)
    frequent = [t for t, f in ann.items() if f >= FREQUENT]
    out = []
    for e in excluded:
        e = hpo.canonical(e)
        if e and e in hpo.descendants(_hpoa.PHENOTYPE_ROOT) and \
                any(e in hpo.ancestors(t) for t in frequent):
            out.append(e)
    return out


def _disease_ids(r: dict) -> list[str]:
    return [r["id"], *(r.get("xrefs") or ())]


# -- rank --------------------------------------------------------------------------------
def rank_case(case: Case, hpo, input: str = "hpo", top: int = 30, holdout_on: bool = True,
              max_terms: int | None = None, imprecision: float = 0.0, noise: int = 0,
              seed: int = 0) -> dict:
    """Rank one patient as main.py would and score where its diagnosis lands."""
    parts = []
    if input in ("hpo", "hpo+gene"):
        parts += [Part(t, "phenotype", t, hpo.name.get(t, t), "id")
                  for t in perturb(case, hpo, max_terms, imprecision, noise, seed)]
    if input in ("gene", "hpo+gene"):
        parts += [Part(g, "gene", g, g, "symbol") for g in case.genes if hpo.gene_symbol(g)]
    res = {"id": case.id, "cohort": case.cohort, "disease": case.disease_id,
           "label": case.disease_label, "n_terms": sum(p.kind == "phenotype" for p in parts),
           "rank": None, "top1": None, "hidden": 0}
    if not parts:
        res["skipped"] = "nothing to search with"
        return res
    truth = truth_keys(case)
    with holdout(hpo, case, holdout_on) as hidden, fast_identity():
        ranking = resolve.rank(Interpretation(case.id, parts), top, hpo=hpo)
        res["hidden"] = hidden
        res["rank"] = find_rank(ranking, truth)
        if ranking:
            t1 = ranking[0]
            res["top1"] = f"{t1['name']} ({t1['id']})"
            res["top1_contradicted"] = contradicted(hpo, _disease_ids(t1), case.excluded)
            res["truth_contradicted"] = contradicted(
                hpo, [i for i in truth if i in hpo.ann], case.excluded)
    return res


def summarize(results: list[dict], ks=(1, 3, 10)) -> dict:
    """Micro (per patient) and macro (per disease) top-k and MRR."""
    rs = [r for r in results if not r.get("skipped")]
    if not rs:
        return {"cases": 0}

    def stats(group):
        out = {f"top{k}": sum(1 for r in group if r["rank"] and r["rank"] <= k) / len(group)
               for k in ks}
        out["mrr"] = sum(1 / r["rank"] for r in group if r["rank"]) / len(group)
        return out
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rs:
        by[r["disease"]].append(r)
    per = [stats(g) for g in by.values()]
    macro = {k: statistics.fmean(p[k] for p in per) for k in per[0]}
    missed = [r for r in rs if r["rank"] != 1]
    rescuable = [r for r in missed if r.get("top1_contradicted")
                 and not r.get("truth_contradicted")]
    return {"cases": len(rs), "diseases": len(by), "skipped": len(results) - len(rs),
            "micro": stats(rs), "macro": macro,
            "top1_contradicted": sum(bool(r.get("top1_contradicted")) for r in rs) / len(rs),
            "truth_contradicted": sum(bool(r.get("truth_contradicted")) for r in rs) / len(rs),
            "rescuable": len(rescuable),
            "hidden_annotations": sum(r["hidden"] for r in rs)}


def run_rank(cases: list[Case], hpo, progress: bool = False, **kw) -> list[dict]:
    out = []
    for i, c in enumerate(cases, 1):
        out.append(rank_case(c, hpo, **kw))
        if progress and i % 25 == 0:
            print(f"  {i}/{len(cases)}", file=sys.stderr, flush=True)
    return out


# -- profile -----------------------------------------------------------------------------
@dataclass
class Cohort:
    """What the patients of one disease have / lack, per HPO term (ontology-aware: a
    patient with "Ectopia lentis" has "Abnormality of the lens"; one without "Abnormality
    of the lens" lacks "Ectopia lentis")."""
    cases: list[Case]
    hpo: object
    _has: dict[str, int] = field(default_factory=dict)
    _lacks: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        h = self.hpo
        for c in self.cases:
            for t in {a for o in c.observed if h.canonical(o)
                      for a in h.ancestors(h.canonical(o))}:
                self._has[t] = self._has.get(t, 0) + 1
            for t in {d for e in c.excluded if h.canonical(e)
                      for d in h.descendants(h.canonical(e))}:
                self._lacks[t] = self._lacks.get(t, 0) + 1

    def has(self, t: str) -> int:
        return self._has.get(t, 0)

    def lacks(self, t: str) -> int:
        return self._lacks.get(t, 0)

    def observed(self) -> Counter:
        """Observed terms as recorded (not propagated): term -> patients."""
        return Counter(t for c in self.cases
                       for t in {self.hpo.canonical(o) for o in c.observed} if t)


def score_profile(symptoms: dict[str, float | None], genes: set[str], cohort: Cohort,
                  graph_variants: set[str] = frozenset(), common: float = 0.25,
                  min_specificity: float = 0.35) -> dict:
    """symptoms: profile HP id -> annotated frequency (None: unknown); genes: the
    profile's disease-causing gene symbols.

    feature_recall    share of the patients' observed features (one count per patient
                      and feature) the profile mentions: the term or a more specific one
                      ("strict"), or also a broader term that is still informative
                      (specificity >= min_specificity, "lenient")
    common_recall     share of the features at least `common` of the patients have
                      (and >= 2 patients) the profile mentions (strict)
    supported         of the profile symptoms the patients were assessed for, the share
                      at least one patient has
    contradicted      "very frequent" profile symptoms that most assessed patients lack
                      (>= 3 assessed)"""
    h = cohort.hpo
    prof = {h.canonical(t) for t in symptoms if h.canonical(t)}
    below = set().union(*(h.ancestors(t) for t in prof)) if prof else set()

    def strict(t):  # the profile has t or a descendant of t
        return t in below

    def lenient(t):
        return strict(t) or any(a in prof and h.specificity(a) >= min_specificity
                                for a in h.ancestors(t))
    obs = cohort.observed()
    n = sum(obs.values()) or 1
    n_pat = len(cohort.cases)
    common_terms = [t for t, k in obs.items() if k >= max(2, common * n_pat)]
    missing = sorted((t for t in common_terms if not strict(t)), key=lambda t: -obs[t])
    assessed = [t for t in prof if cohort.has(t) + cohort.lacks(t)]
    contra = []
    for t in prof:
        f = symptoms.get(t)
        a = cohort.has(t) + cohort.lacks(t)
        if f is not None and f >= FREQUENT and a >= 3 and cohort.lacks(t) / a > 0.5:
            contra.append((t, cohort.lacks(t), a))
    want_genes = Counter(g for c in cohort.cases for g in c.genes)
    want_vars = {v for c in cohort.cases for v in c.variants}
    return {
        "patients": n_pat, "profile_symptoms": len(prof),
        "feature_recall": sum(k for t, k in obs.items() if strict(t)) / n,
        "feature_recall_lenient": sum(k for t, k in obs.items() if lenient(t)) / n,
        "common_recall": (1 - len(missing) / len(common_terms)) if common_terms else None,
        "missing_common": [(t, h.name.get(t, t), obs[t]) for t in missing[:15]],
        "supported": (sum(1 for t in assessed if cohort.has(t)) / len(assessed)
                      if assessed else None),
        "contradicted": [(t, h.name.get(t, t), lk, a) for t, lk, a in
                         sorted(contra, key=lambda x: -x[1])],
        "gene_recall": (sum(1 for g in want_genes if g in genes) / len(want_genes)
                        if want_genes else None),
        "missing_genes": sorted(g for g in want_genes if g not in genes),
        "variant_recall": (len(want_vars & graph_variants) / len(want_vars)
                           if want_vars else None),
        "patient_variants": len(want_vars),
    }


def profile_view(data: dict, focus: str) -> dict:
    """present.py's profile of one focus node (no similar-disease search: not scored)."""
    import present
    return present.Present(present.Graph(data), focus, similar=0).build()


def profile_parts(view: dict, data: dict) -> tuple[dict[str, float | None], set[str],
                                                   set[str]]:
    """(symptom HP id -> frequency, causal gene symbols, graph variant labels)."""
    nodes = {n["id"]: n for n in data["nodes"]}
    syms: dict[str, float | None] = {}
    words = {"always": 1.0, "very frequent": 0.9, "frequent": 0.55, "occasional": 0.17,
             "rare": 0.02}
    for it in view["items"].values():
        if it["section"] != "symptoms":
            continue
        ids = [it["id"], *(nodes.get(it["id"], {}).get("xrefs") or ())]
        hp = next((i for i in ids if i.startswith("HP:")), None)
        if hp:
            lab = (it.get("frequency") or "").split(" (")[0].lower()
            syms[hp] = words.get(lab)
    genes = {it["label"].upper() for g in view["groups"] if g["label"] == "Disease-causing genes"
             for it in (view["items"][i] for i in g["items"])}
    variants = {n["label"] for n in data["nodes"] if n["kind"] == "variant"}
    return syms, genes, variants


def evaluate_run(path: Path, cases: list[Case], hpo) -> list[dict]:
    """Score every focus disease of a main.py graph JSON that has patients."""
    data = json.loads(path.read_text(encoding="utf-8"))
    from entities import normalize
    nodes = {n["id"]: n for n in data["nodes"]}
    by_key: dict[str, list[Case]] = defaultdict(list)
    for c in cases:
        for k in truth_keys(c):
            by_key[k].append(c)
    out = []
    for f in dict.fromkeys(data.get("focus") or ()):
        if f not in nodes:
            continue
        ids = {normalize(x) for x in [f, *(nodes[f].get("xrefs") or ())]}
        pts = list({c.id + c.disease_id: c for k in ids for c in by_key.get(k, ())}.values())
        row = {"run": str(path), "focus": f, "label": nodes[f]["label"], "patients": len(pts)}
        q = data.get("query")
        if q and q.get("ranking"):  # symptom / gene run: where this focus was ranked
            row["ranked"] = next((i for i, r in enumerate(q["ranking"], 1)
                                  if r["node"] == f), None)
        if pts:
            view = profile_view(data, f)
            syms, genes, variants = profile_parts(view, data)
            row.update(score_profile(syms, genes, Cohort(pts, hpo), variants))
        out.append(row)
    return out


# -- CLI ---------------------------------------------------------------------------------
def _pct(x):
    return "  -  " if x is None else f"{100 * x:4.0f}%"


def print_rank(summary: dict, results: list[dict], worst: int = 15):
    if not summary.get("cases"):
        print("no cases")
        return
    mi, ma = summary["micro"], summary["macro"]
    print(f"\n{summary['cases']} patients, {summary['diseases']} diseases"
          + (f" ({summary['skipped']} skipped)" if summary["skipped"] else ""))
    print("            top1   top3  top10   MRR")
    for name, s in (("patients", mi), ("diseases", ma)):
        print(f"  {name:9s} {_pct(s['top1'])} {_pct(s['top3'])} {_pct(s['top10'])}  "
              f"{s['mrr']:.3f}")
    print(f"top-1 contradicted by an excluded term: {_pct(summary['top1_contradicted'])}; "
          f"diagnosis contradicted: {_pct(summary['truth_contradicted'])}; "
          f"misses an excluded-aware score could fix: {summary['rescuable']}")
    print(f"annotations hidden (leave-publication-out): {summary['hidden_annotations']}")
    misses = [r for r in results if not r.get("skipped") and (r["rank"] or 99) > 10]
    if misses:
        print(f"\nnot in the top 10 ({len(misses)}), e.g.:")
        for r in misses[:worst]:
            print(f"  {r['label'][:50]:50s} ({r['disease']}, {r['n_terms']} terms) "
                  f"rank {r['rank'] or '>30'}; top-1 {r['top1']}")


def print_profile(rows: list[dict]):
    for r in rows:
        head = f"\n{r['label']} ({r['focus']}) in {r['run']}"
        if "ranked" in r:
            head += f", ranked {r['ranked']}"
        print(head)
        if not r["patients"]:
            print("  no phenopacket-store patients")
            continue
        print(f"  {r['patients']} patients, {r['profile_symptoms']} profile symptoms")
        print(f"  feature recall {_pct(r['feature_recall'])} strict, "
              f"{_pct(r['feature_recall_lenient'])} lenient; common features "
              f"{_pct(r['common_recall'])}; supported {_pct(r['supported'])}")
        print(f"  causal genes {_pct(r['gene_recall'])}"
              + (f" (missing {', '.join(r['missing_genes'])})" if r["missing_genes"] else "")
              + f"; patient variants in the graph {_pct(r['variant_recall'])} "
              f"of {r['patient_variants']}")
        if r["missing_common"]:
            print("  missing common features: " + "; ".join(
                f"{n} ({k})" for _, n, k in r["missing_common"][:8]))
        if r["contradicted"]:
            print("  'very frequent' but mostly absent in patients: " + "; ".join(
                f"{n} ({lk}/{a} lack it)" for _, n, lk, a in r["contradicted"][:8]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("download", help="fetch the latest phenopacket-store release")
    r = sub.add_parser("rank", help="candidate ranking of patients")
    r.add_argument("--input", choices=INPUTS, default="hpo")
    r.add_argument("--cohort", action="append", help="only this phenopacket-store folder")
    r.add_argument("--per-disease", type=int, default=2)
    r.add_argument("--max-cases", type=int, default=200)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--top", type=int, default=30, help="ranked candidates looked at")
    r.add_argument("--max-terms", type=int)
    r.add_argument("--imprecision", type=float, default=0.0)
    r.add_argument("--noise", type=int, default=0)
    r.add_argument("--no-holdout", action="store_true")
    r.add_argument("--json", type=Path, help="write per-patient results + summary")
    improvements.add_argument(r)
    p = sub.add_parser("profile", help="profiles in main.py graph JSONs vs patients")
    p.add_argument("runs", nargs="+", type=Path)
    p.add_argument("--json", type=Path)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    args = ap.parse_args(argv)
    if args.cmd == "download":
        print(download())
        return
    if not ZIP.exists():
        ap.error(f"{ZIP} missing: run `phenobench.py download` first")
    hpo = _hpoa.load()
    if hpo is None:
        ap.error("local HPO data unavailable")
    if args.cmd == "rank":
        improvements.apply_args(args)
        cases = sample(load_cases(ZIP, args.cohort), args.per_disease, args.max_cases,
                       args.seed)
        print(f"ranking {len(cases)} patients (input {args.input}, improvements "
              f"{args.improvements}, holdout {'off' if args.no_holdout else 'on'})",
              file=sys.stderr)
        results = run_rank(cases, hpo, progress=True, input=args.input, top=args.top,
                           holdout_on=not args.no_holdout, max_terms=args.max_terms,
                           imprecision=args.imprecision, noise=args.noise, seed=args.seed)
        summary = summarize(results)
        print_rank(summary, results)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps({"args": {k: str(v) for k, v in vars(args).items()},
                                             "summary": summary, "results": results},
                                            indent=1, ensure_ascii=False), encoding="utf-8")
    else:
        cases = load_cases(ZIP)
        rows = [row for path in args.runs for row in evaluate_run(path, cases, hpo)]
        print_profile(rows)
        if args.json:
            args.json.write_text(json.dumps(rows, indent=1, ensure_ascii=False),
                                 encoding="utf-8")


if __name__ == "__main__":
    main()
