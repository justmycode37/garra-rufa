"""llm_rerank (improvements.py): a language model re-orders the top of a candidate ranking.

The phenotype score of _hpoa.rank_diseases only sees HPO annotations; for diseases
described in a single paper those are few, and it cannot weigh the gestalt of a
presentation ("which of these is the patient most likely to have"). The model gets the
patient's present and absent phenotypes and genes, and for each of the top `n`
candidates its causal genes and most characteristic annotated phenotypes, and returns
the candidates' order. Candidates it leaves out keep their place after the ones it
ordered; everything below `n` is unchanged.

Needs OPENROUTER_API_KEY (evidence/llm.py, responses cached); without it, or when the
call fails, the ranking is returned unchanged.

  prompt(ranking, phenotypes, genes, hpo, excluded, n) -> (system, user)
  apply(ranking, reply, n)                             -> re-ordered ranking
  rerank(ranking, ..., llm=None)                       -> both, one call
"""
import os
import sys

from sources import _hpoa

N = 20  # candidates shown to the model
FEATURES = 12  # characteristic phenotypes shown per candidate

SYSTEM = (
    "You are a clinical geneticist. A phenotype-matching tool ranked candidate rare "
    "diseases for a patient. Using your knowledge of these diseases and the annotations "
    "given, re-order the candidates by how likely each one is the patient's diagnosis. "
    "Weigh specific and characteristic findings over common ones; a patient rarely has "
    "every feature of a disease, and explicitly absent findings speak against diseases in "
    "which they are (nearly) always present. Answer with JSON only: "
    '{"order": [candidate numbers, most likely first], "reason": "<one sentence>"}')

FREQ = ((0.99, "always"), (0.8, "very frequent"), (0.3, "frequent"), (0.05, "occasional"),
        (0.0, "rare"))


def _freq_word(f: float) -> str:
    if f == _hpoa.UNKNOWN_FREQ:
        return ""
    return next(w for lo, w in FREQ if f >= lo)


def features(hpo, ids: list[str], k: int = FEATURES) -> list[str]:
    """The k most characteristic annotated phenotypes of a disease (all its OMIM / ORPHA
    entries): informative (IC) and frequent first."""
    ann: dict[str, float] = {}
    for i in ids:
        for hp, f in hpo.ann.get(i, {}).items():
            ann[hp] = max(ann.get(hp, 0.0), f)
    best = sorted(ann, key=lambda t: -hpo.specificity(t) * (0.5 + ann[t]))[:k]
    return [hpo.name[t] + (f" ({w})" if (w := _freq_word(ann[t])) else "") for t in best]


def causal_genes(hpo, ids: list[str]) -> list[str]:
    out = {g for g, ds in hpo.g2d.items() for d, a in ds if d in ids and _hpoa.causal(a)}
    return sorted(out)


def prompt(ranking: list[dict], phenotypes: list[str], genes: list[str], hpo,
           excluded: list[str] = (), n: int = N) -> tuple[str, str]:
    lines = ["Patient",
             "  present: " + ("; ".join(hpo.name.get(t, t) for t in phenotypes) or "-")]
    if excluded:
        lines.append("  explicitly absent: " + "; ".join(hpo.name.get(t, t) for t in excluded))
    if genes:
        lines.append("  gene(s) with a candidate variant: " + ", ".join(genes))
    lines += ["", "Candidates (tool order)"]
    for i, r in enumerate(ranking[:n], 1):
        ids = [r["id"], *(r.get("xrefs") or ())]
        g = causal_genes(hpo, ids)
        lines.append(f"{i}. {r['name']} ({r['id']})" + (f"; genes: {', '.join(g)}" if g else ""))
        f = features(hpo, ids)
        lines.append("   " + ("; ".join(f) if f else "no phenotype annotations"))
    return SYSTEM, "\n".join(lines)


def apply(ranking: list[dict], reply: dict | None, n: int = N) -> list[dict]:
    head, tail = ranking[:n], ranking[n:]
    order = []
    for x in (reply or {}).get("order") or ():
        try:
            i = int(x) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(head) and i not in order:
            order.append(i)
    if not order:
        return ranking
    order += [i for i in range(len(head)) if i not in order]
    return [{**head[i], "tool_rank": i + 1} for i in order] + tail


_llm = None


def default_llm():
    """evidence/llm.py's client with the repo .env loaded, or None without a key."""
    global _llm
    if _llm is None:
        from evidence.llm import Llm
        from evidence.main import load_env
        from literature.base import Cache
        load_env()
        if not os.environ.get("OPENROUTER_API_KEY"):
            _llm = False
        else:
            _llm = Llm(Cache())
    return _llm or None


def rerank(ranking: list[dict], phenotypes: list[str], genes: list[str], hpo,
           excluded: list[str] = (), n: int = N, llm=None) -> list[dict]:
    """The ranking with its top n re-ordered by the model (unchanged on any failure)."""
    pinned = [r for r in ranking if r.get("pinned")]
    rest = [r for r in ranking if not r.get("pinned")]
    if len(rest) < 2 or not phenotypes:
        return ranking
    llm = llm or default_llm()
    if llm is None:
        return ranking
    try:
        reply = llm.chat(*prompt(rest, phenotypes, genes, hpo, excluded, n), max_tokens=8000)
    except Exception as e:  # the ranking stays usable without the model
        print(f"[rerank] model call failed, keeping the tool order: {e}", file=sys.stderr)
        return ranking
    return pinned + apply(rest, reply, n)
