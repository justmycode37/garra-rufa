"""Merge the per-paper extractions into one evidence graph and rank candidate solutions.

Nodes are keyed by their normalised id (normalize.py), edges by (subject, predicate,
object); an edge keeps every verified quote from every paper. Edge confidence is a
noisy-OR over the papers that support it, each weighted by its evidence level (LEVEL_W,
x ABSTRACT_W when only the abstract was read); papers reporting a negative / null effect
are counted separately and never add support.

Candidate solutions for the input disease D: every solution node S (drug, therapy,
model_system, biomarker, assay, diagnostic, outcome_measure, resource, method) with an
evidenced edge S -[SOLUTION_PREDICATES]-> X is connected to D through a bridge:
  X is D                                    same disease                        1.0
  X -shares_mechanism_with- D (evidence)    stated by a paper                   0.9
  X and D share a neighbour M               gene (causal 1.0, other 0.5), pathway 0.8,
     (evidence graph or source graph)       process 0.6, cell type 0.5, anatomy 0.4,
                                            phenotypes 0.15 each (max 0.6)
  X is D's ontology parent / child          source graph hierarchy              0.6
  X and D share an ontology parent                                              0.4
  X is a gene / pathway / process / cell type / anatomy / phenotype linked to D
                                            weight of that kind as above
path score = edge confidence x bridge weight (x NEGATIVE_W when most papers on the edge
report no or a negative effect; negative / null evidence only counts from primary
studies, not reviews); a candidate's score is the noisy-OR over its paths.
Where a result was obtained decides the category, not the edge's object: a solution is
"direct" when any of its edges was obtained in D (its patients or patient cells:
evidence context_disease in D or D's ontology subtypes) or in a model of D (context_model,
or the object, is a model_system with an evidenced "models" edge to D), or targets D
itself, or a registered trial / Open Targets lists it for D. Such an edge also gets the
bridge "tested in <model> (model of the input disease)" (CONTEXT_W). Only solutions never
tested in D or its models are "transfer" candidates. Each becomes an inferred
"may_accelerate" edge S -> D; "found_by" says which step brought its evidence (core
literature, the transfer search of transfer.py, registries.py).

Nodes of one kind with the same normalised label but different ids (an ontology id and a
merged source-graph id for one disease) are merged into one, preferring the id the source
graph uses. Papers are related when they support the same edges or name the same entities,
not counting background ones most papers share (paper_links).

Improvements (../improvements.py, each switchable):
  input_identity   nodes whose id is one of the input's xrefs are merged into the input;
                   disease nodes whose label is a qualified form of an input name ("late-
                   onset Pompe disease", "Niemann-Pick disease type C1") or the unqualified
                   parent of the input label ("Rett syndrome" for "Atypical Rett
                   syndrome") count as the input (input_ids, bridge 1.0, their mechanism
                   neighbours are the input's)
  canonicalize     merge key = canon_key (case / accents folded, Greek letters spelled,
                   drug salt / formulation suffixes dropped, drug + therapy one group),
                   readable labels (no ALL CAPS); canonicalize_llm() groups same-concept
                   labels of each solution kind with one cached LLM call per batch
  paper_scoring    candidate score = noisy-OR over its papers (each paper's best path:
                   paper level weight x bridge) x TIER_W of the evidence tier (3 clinical /
                   registered / approved, or a registry row that it is tested in the input;
                   2 replicated or human data, or two papers (even reviews) saying it treats
                   the input; 1 single preclinical paper); n_papers, best_level, tier
  generic_penalty  ubiquitous techniques (is_generic) x GENERIC_W, flagged generic; transfer
                   outcome measures without a shared phenotype / process / symptom bridge
                   x OTHER_ENDPOINT_W, flagged other_disease_endpoint; study designs,
                   statistics, trial names / registry ids and patient groups
                   (is_non_solution) are not ranked (Graph.non_solutions)
  symptom_axis     a phenotype the user typed (symptom mode) bridges with SYMPTOM_W
"""
import re
import unicodedata
from collections import Counter, defaultdict
from itertools import combinations

import improvements

from .context import CAUSAL, GENERIC, HIERARCHY, key
from .extract import SOLUTION_TYPES

LEVEL_W = {"clinical_trial": 0.9, "observational": 0.7, "case_report": 0.5, "animal": 0.5,
           "in_vitro": 0.35, "in_silico": 0.2, "review": 0.2,
           # registries.py: a registered trial (no results yet) / a drug-target database row
           "registered_trial": 0.6, "database": 0.4}
CONTEXT_W = {"patients": 1.0, "model": 0.9}
ABSTRACT_W = 0.7
SECONDHAND = ("review", "in_silico")
NEGATIVE_W = 0.25
SOLUTION_PREDICATES = frozenset({"treats", "rescues", "tested_in", "models", "biomarker_for",
                                 "diagnoses", "measures_outcome_of", "targets",
                                 "applicable_to"})
MECHANISM_PREDICATES = frozenset({"causes", "involves", "has_phenotype",
                                  "shares_mechanism_with"})
KIND_W = {"gene": 0.5, "pathway": 0.8, "process": 0.6, "cell_type": 0.5, "cell": 0.5,
          "anatomy": 0.4}
PHENOTYPE_EACH, PHENOTYPE_MAX = 0.15, 0.6
# -- improvements (see module docstring) ------------------------------------------------
SYMPTOM_W = 0.7
TIER_W = {3: 1.0, 2: 0.8, 1: 0.5}
CLINICAL = ("clinical_trial", "registered_trial")
HUMAN = ("clinical_trial", "registered_trial", "observational")
APPROVED_RX = re.compile(r"\b(stage: (approv\w*|phase 4)|fda approved|approved by the|"
                         r"marketing authori[sz]ation)", re.I)
GENERIC_W, OTHER_ENDPOINT_W = 0.3, 0.1
GENERIC_RX = re.compile(
    r"\b(sequencing|exome|whole genome|genome wide|sanger|ngs|mri|magnetic resonance|"
    r"computed tomography|ct|ct scan|pet|ultrasound|ultrasonography|sonography|"
    r"echocardiograph\w*|x ray|radiograph\w*|imaging|western blot\w*|immunoblot\w*|"
    r"blotting|histolog\w*|h e staining|hematoxylin|haematoxylin|histopatholog\w*|"
    r"histochemi\w*|elisa|enzyme linked immunosorbent assays?|chip|"
    r"chip seq|chromatin immunoprecipitation|facs|fluorescence activated cell sorting|"
    r"qpcr|pcr|rt pcr|real time pcr|rna seq|transcriptom\w*|proteom\w*|mass spectrometry|"
    r"spectrometry|immunohistochem\w*|immunofluorescen\w*|immunostaining|microscopy|"
    r"flow cytometry|genotyping|microarray|karyotyp\w*|biopsy|electrophoresis|"
    r"chromatography|gene panel|genetic testing|molecular testing|hplc|uplc|lc ms|"
    r"lc ms ms|gc ms|uplc ms|hplc ms|liquid chromatography|gas chromatography|"
    r"tandem mass|nmr|nuclear magnetic resonance|spectroscopy|spectrophotometr\w*|"
    r"fluorometr\w*|immunoassays?|plasma concentrations?|pharmacokinetic\w*|cmax|"
    r"half life)\b")
# words a generic technique name may carry besides GENERIC_RX matches and still be generic
# ("high-performance liquid chromatography-mass spectrometry")
TECH_FILLER = frozenset({"high", "performance", "ultra", "tandem", "coupled", "with", "to",
                         "and", "of", "the", "quantitative", "targeted", "untargeted",
                         "analysis", "analyses", "method", "methods", "assay", "based",
                         "liquid", "gas", "mass", "time", "flight", "real", "reverse",
                         "phase", "electrospray", "ionization", "ms", "pressure"})
# -- non-solutions: study designs, statistics, trial names / registry ids, patient groups
DESIGN_WORDS = frozenset({
    "prospective", "retrospective", "open", "label", "trial", "trials", "study", "studies",
    "randomized", "randomised", "double", "single", "triple", "blind", "blinded", "masked",
    "placebo", "controlled", "control", "crossover", "phase", "multicenter", "multicentre",
    "multinational", "pilot", "observational", "longitudinal", "cross", "sectional", "case",
    "series", "cohort", "design", "parallel", "group", "arm", "extension", "interventional",
    "clinical", "dose", "escalation", "finding", "ranging", "sham", "a", "an", "the", "of",
    "with", "and", "i", "ii", "iii", "iv", "1", "2", "3", "4", "1b", "2a", "2b", "3b",
    "first", "in", "human", "label", "non", "inferiority", "feasibility", "natural",
    "history", "period", "baseline", "adaptive", "seamless", "basket", "umbrella"})
STAT_RX = re.compile(
    r"\b(fisher s exact|fishers exact|exact test|chi square|chi squared|t test|student s t|"
    r"students t|wilcoxon|mann whitney|kruskal wallis|anova|ancova|manova|linear regression|"
    r"logistic regression|cox regression|poisson regression|regression analys\w*|"
    r"regression models?|"
    r"generali[sz]ed estimating equations?|mixed effects?|mixed models?|linear models?|"
    r"kaplan meier|cox proportional|cox model|log rank|bonferroni|spearman|pearson|"
    r"correlation coefficient|statistical|statistics|bayesian|propensity score|"
    r"intention to treat|per protocol|last observation carried|imputation|bootstrap\w*|"
    r"false discovery rate|benjamini|hochberg|post hoc|tukey|dunnett|sample size|"
    r"power calculation|meta analysis|p value|odds ratio|hazard ratio|confidence interval)\b")
REGISTRY_RX = re.compile(r"\b(nct\d{6,}|clinicaltrials gov|isrctn\w*|eudract\w*|drks\d*|"
                         r"chictr\w*|actrn\w*|jrct\w*|trial identifier|trial registration|"
                         r"registration number|identifier)\b")
TRIAL_NAME = re.compile(r"^[A-Z][A-Z0-9]{2,}(?:[- ][A-Z0-9]{1,4})*(?:[- ]\d{1,3})?"
                        r"(?: (?:trial|study|extension))?$")
GROUP_WORDS = frozenset({"patients", "patient", "cohort", "cohorts", "subjects",
                         "participants", "individuals", "children", "adults", "girls",
                         "boys", "women", "men", "families", "population", "populations",
                         "controls", "volunteers", "carriers", "probands", "siblings",
                         "caregivers", "parents", "infants", "neonates", "adolescents"})
RESOURCE_WORDS = frozenset({"registry", "database", "biobank", "consortium", "network",
                            "repository", "atlas", "platform", "natural", "dataset"})
# dataset accessions are resources, not trial names (GEO, SRA, BioProject, ArrayExpress)
DATASET_RX = re.compile(r"^(GSE|GDS|GSM|SRP|SRR|PRJNA|PRJEB|E-MTAB-|E-GEOD-)\d", re.I)
# a label ending in a group word that is still a solution: a scale, cells, a model
NOT_GROUP = frozenset({"scale", "score", "questionnaire", "inventory", "index", "fibroblasts",
                       "cells", "cell", "derived", "lines", "line", "ipsc", "ipscs",
                       "organoids", "neurons", "model", "models", "mice", "mouse", "rats"})
# words that make a technique a specific measurement ("MRI fat fraction")
MEASURE_WORDS = frozenset({"fraction", "score", "volume", "index", "ratio", "activity",
                           "level", "levels", "quantification", "signal", "rate", "count",
                           "area", "thickness", "mass", "content", "burden"})
GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta",
         "η": "eta", "θ": "theta", "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu",
         "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho", "σ": "sigma", "ς": "sigma", "τ": "tau",
         "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega"}
SALTS = frozenset({
    "hydrochloride", "dihydrochloride", "trihydrochloride", "hcl", "citrate", "sodium",
    "disodium", "trisodium", "potassium", "dipotassium", "calcium", "magnesium", "mesylate",
    "mesilate", "dimesylate", "besylate", "besilate", "tosylate", "maleate", "fumarate",
    "hemifumarate", "tartrate", "bitartrate", "succinate", "sulfate", "sulphate", "bisulfate",
    "phosphate", "diphosphate", "acetate", "diacetate", "bromide", "hydrobromide", "chloride",
    "iodide", "nitrate", "lactate", "gluconate", "malate", "oxalate", "palmitate", "stearate",
    "trifluoroacetate", "tfa", "monohydrate", "dihydrate", "trihydrate", "hemihydrate",
    "hydrate", "anhydrous", "salt", "free", "base", "lysine", "meglumine", "tromethamine",
    "hyclate", "hydroxide", "carbonate", "bicarbonate", "ester", "formulation",
    "tablet", "tablets", "capsule", "capsules", "injection", "oral", "solution",
    "suspension", "er", "xr", "sr"})
INTERVENTION = frozenset({"drug", "therapy"})
# words that only qualify a disease name: "Atypical Rett syndrome" -> "Rett syndrome"
QUALIFIERS = frozenset({
    "atypical", "classic", "classical", "typical", "infantile", "juvenile", "adult",
    "adolescent", "childhood", "neonatal", "congenital", "late", "early", "onset", "variant",
    "variants", "form", "forms", "subtype", "severe", "mild", "attenuated", "intermediate",
    "progressive", "rapidly", "slowly", "non", "crim", "negative", "positive", "cross",
    "reactive", "immunologic", "immunological", "material", "patients", "phenotype",
    "presentation", "onset", "the", "of", "with", "in", "human"})
# remainder words that make a name containing the input name a different thing
NOT_SAME = frozenset({"like", "related", "associated", "mimicking", "versus", "vs", "and",
                      "or", "carrier", "carriers", "model", "models", "mouse", "mice", "rat",
                      "zebrafish", "cells", "cell", "differential"})
DESIGNATOR = re.compile(r"^(?:[ivx]{1,4}|\d+[a-z]?|[a-z]\d{0,2})$")
CANON_SYSTEM = """You deduplicate entity names extracted from biomedical papers for a
knowledge graph. All names below have the same type. Group names that denote EXACTLY the
same concept: spelling, hyphenation, word-order or plural variants, an abbreviation and its
expansion, synonyms, and for drugs the salt forms, INN, brand and code names of one
compound. Never group: different measures of one thing (volume vs number vs incidence),
different time points, scales, methods or units, different species, strains, cell lines or
models, a broader and a narrower concept, a drug and its target, different doses,
combinations or delivery constructs. When in doubt, leave names apart.
Answer with one JSON object: {"groups": [[<index>, <index>, ...], ...]}, listing only groups
of two or more indices; names without a duplicate are omitted."""


def noisy_or(ws) -> float:
    p = 1.0
    for w in ws:
        p *= 1 - max(0.0, min(1.0, w))
    return round(1 - p, 3)


def _fold(label: str) -> str:
    """Lower case, Greek letters spelled out, accents removed ("β" -> "beta", "ö" -> "o")."""
    t = (label or "").casefold()
    t = "".join(f" {GREEK[c]} " if c in GREEK else c for c in t)
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


def canon_key(label: str, kind: str = "") -> str:
    """Merge key of a label: key() of the folded label; drugs / therapies without trailing
    salt or formulation words ("momelotinib dihydrochloride" -> "momelotinib")."""
    k = key(_fold(label))
    if kind in INTERVENTION:
        ws = k.split()
        while len(ws) > 1 and ws[-1] in SALTS:
            ws.pop()
        k = " ".join(ws)
    return k


def kind_group(kind: str) -> str:
    return "intervention" if kind in INTERVENTION else kind


def is_shouting(label: str) -> bool:
    """An ALL CAPS name ("MOMELOTINIB DIHYDROCHLORIDE"), not an acronym ("GLM101")."""
    letters = [c for c in label or "" if c.isalpha()]
    return len(letters) >= 6 and label.upper() == label and \
        any(len(w) >= 5 for w in re.findall(r"[A-Za-z]+", label))


def readable(label: str) -> str:
    return label.lower() if is_shouting(label) else label


def is_generic(label: str, specific: set[str] = frozenset()) -> bool:
    """A ubiquitous technique ("Sanger sequencing", "brain MRI", "western blotting"): a
    GENERIC_RX word in a short label (<= 4 words) that names no measurement (MEASURE_WORDS)
    and no word of `specific` (the input's gene symbols / disease name words)."""
    k = key(_fold(label))
    ws = k.split()
    if not ws or not GENERIC_RX.search(k) or set(ws) & specific:
        return False
    rest = set(GENERIC_RX.sub(" ", k).split())  # words besides the technique's own
    if rest & MEASURE_WORDS:
        return False
    # longer than 4 words: generic only when nothing but technique words ("high-performance
    # liquid chromatography-mass spectrometry")
    return len(ws) <= 4 or not (rest - TECH_FILLER)


def is_non_solution(label: str, kind: str = "") -> str:
    """Why an extracted "solution" is none (generic_penalty), else "": a study design
    ("prospective open-label trial"), a statistical method ("Fisher's exact test", "inverse
    Kaplan-Meier method"), a trial name / registry id ("LUMINA-1", "OPTIMA trial",
    "NCT02190747", "ClinicalTrials.gov identifier") or a patient group ("Bardet-Biedl
    syndrome patients"). Drugs and therapies are never non-solutions; named registries,
    databases and natural-history studies stay resources."""
    if kind in INTERVENTION:
        return ""
    raw = (label or "").strip()
    k = key(_fold(raw))
    ws = k.split()
    if not ws:
        return ""
    if REGISTRY_RX.search(k):
        return "trial registry id"
    if STAT_RX.search(k):
        return "statistical method"
    if set(ws) & RESOURCE_WORDS or DATASET_RX.match(raw):
        return ""
    if all(w in DESIGN_WORDS for w in ws):
        return "study design"
    if TRIAL_NAME.match(raw) and any(c.isdigit() for c in raw) and \
            kind in ("resource", "method", "outcome_measure", "") or \
            (ws[-1] in ("trial", "trials") and len(ws) <= 3 and kind in ("resource", "method")):
        return "trial name"
    if ws[-1] in GROUP_WORDS and not set(ws) & NOT_GROUP:
        return "patient group"
    return ""


def _designators(words: list[str]) -> set[str]:
    return {w for w in words if DESIGNATOR.match(w)}


def _extends(sub: str, base: str) -> bool:
    """A subtype designator of base: "c1" of "c", "1a" of "1" (not "iii" of "ii")."""
    ext = sub[len(base):]
    if not sub.startswith(base) or not ext or len(ext) > 2:
        return False
    return (len(base) == 1 and base.isalpha() and ext.isdigit()) or \
        (base.isdigit() and ext.isalpha() and len(ext) == 1)


def qualified_match(label: str, names: list[str]) -> str | None:
    """Why a disease label is the input under another name, or None (input names of one
    word or in plural, a group, are not used):
      "qualified"  the label contains an input name (>= 2 words) as a word sequence, the
                   other words only qualify it ("late-onset Pompe disease"); a type letter
                   may be extended ("type C" -> "type C1"), a different type designator
                   ("type A") or a word of NOT_SAME ("Pompe-like") rejects it
      "parent"     an input name is the label plus QUALIFIERS only ("Atypical Rett
                   syndrome" -> "Rett syndrome")"""
    w = key(_fold(label)).split()
    if len(w) < 2:
        return None
    nms = [key(_fold(n)).split() for n in names if n and ";" not in n]
    # >= 2 words; no plural group names ("progeroid laminopathies")
    nms = [n for n in nms if len(n) >= 2 and not re.search(r"(?<![su])s$", n[-1])]
    known = set().union(*(_designators(n) for n in nms)) if nms else set()
    for n in nms:
        m = len(n)
        for i in range(len(w) - m + 1):
            seg = w[i:i + m]
            if seg[:-1] != n[:-1]:
                continue
            if seg[-1] != n[-1] and not _extends(seg[-1], n[-1]):
                continue
            rest = w[:i] + w[i + m:]
            if set(rest) & NOT_SAME:
                continue
            if any(not any(d == k or _extends(d, k) or _extends(k, d) for k in known)
                   for d in _designators(rest)):
                continue
            return "qualified" if rest or seg[-1] != n[-1] else "same name"
    for n in nms:  # the label is the unqualified parent of an input name
        m = len(w)
        for i in range(len(n) - m + 1):
            if n[i:i + m] == w:
                rest = n[:i] + n[i + m:]
                if rest and all(x in QUALIFIERS for x in rest):
                    return "parent"
    return None


class Graph:
    def __init__(self, profile):
        self.profile = profile
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}
        self._nb: dict[str, dict] = {}  # _nbrs cache, valid once all papers are added
        self.origins: dict[str, str] = {}  # paper key -> core | transfer | registry
        self._ins: set[str] | None = None  # input_ids cache, reset by merges
        self.non_solutions: list[dict] = []  # generic_penalty: excluded by candidates()
        d = profile.disease
        self.node(d["id"], d["label"], "disease", "graph", d.get("xrefs") or [])

    def node(self, nid, label, kind, how, xrefs=(), name=None, paper=None) -> dict:
        n = self.nodes.get(nid)
        if n is None:
            n = self.nodes[nid] = {"id": nid, "label": label, "kind": kind, "how": how,
                                   "names": [], "xrefs": list(xrefs), "papers": [],
                                   "in_source_graph": nid in self.profile.kinds}
        if name and name not in n["names"]:
            n["names"].append(name)
        if paper and paper not in n["papers"]:
            n["papers"].append(paper)
        return n

    def add_paper(self, rec: dict):
        """rec: key, meta, text_source, entities {k: {name, type, norm}}, edges (verified)."""
        pk = rec["key"]
        self.origins[pk] = rec.get("origin") or "core"
        ids = {}
        for k, e in rec["entities"].items():
            r = e["norm"]
            self.node(r["id"], r["label"], e["type"], r["how"], r.get("xrefs") or [],
                      e["name"], pk)
            ids[k] = r["id"]
        for ed in rec["edges"]:
            s, o = ids[ed["subject"]], ids[ed["object"]]
            if s == o:
                continue
            k = (s, ed["predicate"], o)
            agg = self.edges.setdefault(k, {"from": s, "relation": ed["predicate"], "to": o,
                                            "evidence": [], "reviewed": False,
                                            "status": "unreviewed_extraction"})
            for q in ed["evidence"]:
                agg["evidence"].append({
                    "paper": pk, "pmid": rec["meta"].get("pmid"), "year": rec["meta"].get("year"),
                    "title": rec["meta"].get("title"), "text": rec["text_source"],
                    "level": ed["evidence_level"], "effect": ed["effect"],
                    "organism": ed["organism"], "passage": q["passage"],
                    **{k: ed.get(k, "unknown") for k in
                       ("direction", "polarity", "tissue", "model", "population", "variant")},
                    "limitations": ed.get("limitations", []), "reviewed": False,
                    "verification": q.get("verification", "legacy_unverified"),
                    "context_disease": ids.get(ed.get("context_disease") or ""),
                    "context_model": ids.get(ed.get("context_model") or ""),
                    "section": q.get("section"), "quote": q["quote"]})

    def merge_duplicates(self) -> int:
        """Merge duplicate nodes; returns how many went. Without improvements nothing is
        merged: stable identical IDs already share a node and labels cannot prove
        equivalence. canonicalize: same canon_key per kind_group; input_identity: the
        input's xref-id nodes join the input."""
        canon = improvements.on("canonicalize")
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for n in self.nodes.values() if canon else ():
            groups[(kind_group(n["kind"]), canon_key(n["label"], n["kind"]))].append(n)
        comps = [ns for (_, k), ns in groups.items() if k and len(ns) >= 2]
        if improvements.on("input_identity"):
            d = self.profile.id
            dx = set(self.profile.disease.get("xrefs") or [])
            same = [n for n in self.nodes.values() if n["id"] != d and n["kind"] == "disease"
                    and (n["id"] in dx or d in n["xrefs"])]
            if same and d in self.nodes:
                comps.append([self.nodes[d], *same])
        n = self._merge(comps)
        if canon:
            for nd in self.nodes.values():
                if nd["kind"] in INTERVENTION and is_shouting(nd["label"]):
                    if nd["label"] not in nd["names"]:
                        nd["names"].append(nd["label"])
                    nd["label"] = readable(nd["label"])
        return n

    def _merge(self, comps: list[list[dict]]) -> int:
        """Merge each list of nodes (overlapping lists are joined) into its best node: the
        input, then source-graph ids, ontology ids, most papers. Edges and evidence
        contexts follow; returns how many nodes went."""
        parent: dict[str, str] = {}

        def find(x):
            while parent.get(x, x) != x:
                x = parent[x]
            return x
        for ns in comps:
            for n in ns[1:]:
                a, b = find(ns[0]["id"]), find(n["id"])
                if a != b:
                    parent[b] = a
        members: dict[str, list[dict]] = defaultdict(list)
        for i in list(parent):
            if i in self.nodes:
                members[find(i)].append(self.nodes[i])
        alias = {}
        d = self.profile.id
        canon = improvements.on("canonicalize")
        for root, ns in members.items():
            if root in self.nodes and all(n["id"] != root for n in ns):
                ns.append(self.nodes[root])
            if len(ns) < 2:
                continue
            ns.sort(key=lambda n: (n["id"] != d, not n["in_source_graph"], n["how"] == "text",
                                   -len(n["papers"]), n["id"]))
            keep = ns[0]
            for n in ns[1:]:
                alias[n["id"]] = keep["id"]
                for f in ("names", "papers"):
                    keep[f] += [x for x in n[f] if x not in keep[f]]
                keep["xrefs"] += [x for x in [n["id"], *n["xrefs"]]
                                  if x not in keep["xrefs"] and x != keep["id"]]
                del self.nodes[n["id"]]
            if canon and keep["id"] != d:
                # a readable label: no ALL CAPS, no salt suffix, then the most used one
                best = min((n["label"] for n in ns), key=lambda lab: (
                    is_shouting(lab), len(key(lab).split()), -sum(
                        len(n["papers"]) for n in ns if n["label"] == lab), lab))
                if best != keep["label"]:
                    if keep["label"] not in keep["names"]:
                        keep["names"].append(keep["label"])
                    keep["label"] = readable(best)
        if alias:
            edges, self.edges = self.edges, {}
            for (s, p, o), e in edges.items():
                s, o = alias.get(s, s), alias.get(o, o)
                if s == o:
                    continue
                agg = self.edges.setdefault((s, p, o), {"from": s, "relation": p, "to": o,
                                                        "evidence": []})
                agg["evidence"] += e["evidence"]
            for e in self.edges.values():
                for ev in e["evidence"]:
                    for f in ("context_disease", "context_model"):
                        if ev.get(f) in alias:
                            ev[f] = alias[ev[f]]
            self._nb.clear()
        self._ins = None
        return len(alias)

    def canonicalize_llm(self, llm, kinds=SOLUTION_TYPES, batch: int = 250) -> int:
        """canonicalize: one LLM call per kind group and batch of labels (sorted, so the
        cached reply is reused on a rerun) groups names of one concept ("heterotopic
        ossification volume" / "volume of new heterotopic ossification"); each group is
        merged. Failed calls are skipped. Returns how many nodes went."""
        if llm is None:
            return 0
        by: dict[str, list[dict]] = defaultdict(list)
        for n in self.nodes.values():
            if n["kind"] in kinds and n["id"] != self.profile.id:
                by[kind_group(n["kind"])].append(n)
        comps = []
        for kg, ns in sorted(by.items()):
            ns.sort(key=lambda n: (canon_key(n["label"], n["kind"]), n["id"]))
            for b in range(0, len(ns), batch):
                chunk = ns[b:b + batch]
                if len(chunk) < 2:
                    continue
                user = (f"TYPE: {kg.replace('_', ' ')}\nNAMES:\n"
                        + "\n".join(f"[{i}] {n['label']}" for i, n in enumerate(chunk)))
                try:
                    out = llm.chat(CANON_SYSTEM, user, max_tokens=16000)
                except Exception as e:
                    print(f"  ! canonicalize {kg}: {type(e).__name__} {str(e)[:160]}")
                    continue
                for grp in (out or {}).get("groups") or []:
                    try:
                        idx = sorted({int(i) for i in grp if 0 <= int(i) < len(chunk)})
                    except (TypeError, ValueError):
                        continue
                    if len(idx) >= 2:
                        comps.append([chunk[i] for i in idx])
        return self._merge(comps)

    def paper_links(self, min_shared: int = 2, common: float = 0.2) -> list[dict]:
        """Pairs of papers that support the same edges / name the same entities (at least
        min_shared). Edges and entities in more than `common` of the papers (the disease,
        its gene, "PMM2 causes PMM2-CDG") are background and link nothing."""
        n_papers = len({p for n in self.nodes.values() for p in n["papers"]})
        cap = max(3, common * n_papers)
        edges, ents = Counter(), Counter()
        for e in self.edges.values():
            ps = sorted({ev["paper"] for ev in e["evidence"]})
            if len(ps) <= cap:
                for a, b in combinations(ps, 2):
                    edges[(a, b)] += 1
        for n in self.nodes.values():
            if n["id"] != self.profile.id and len(n["papers"]) <= cap:
                for a, b in combinations(sorted(n["papers"]), 2):
                    ents[(a, b)] += 1
        out = [{"a": a, "b": b, "shared_edges": edges[(a, b)], "shared_entities": ents[(a, b)]}
               for a, b in set(edges) | set(ents)
               if edges[(a, b)] or ents[(a, b)] >= min_shared]
        out.sort(key=lambda x: (-x["shared_edges"], -x["shared_entities"], x["a"], x["b"]))
        return out

    # -- scoring -------------------------------------------------------------------
    def score_edges(self):
        for e in self.edges.values():
            per_paper: dict[str, dict] = {}
            for ev in e["evidence"]:
                if ev["effect"] in ("negative", "null") and ev["level"] in SECONDHAND:
                    continue  # a negative result needs the paper's own data
                w = LEVEL_W.get(ev["level"], 0.2) * (ABSTRACT_W if ev["text"] == "abstract"
                                                     else 1)
                cur = per_paper.get(ev["paper"])
                if cur is None or w > cur["w"]:
                    per_paper[ev["paper"]] = {"w": w, "effect": ev["effect"]}
            sup = [p["w"] * (0.5 if p["effect"] == "mixed" else 1) for p in per_paper.values()
                   if p["effect"] in ("positive", "na", "mixed")]
            neg = [p for p in per_paper.values() if p["effect"] in ("negative", "null")]
            if improvements.on("paper_scoring"):  # supporting papers' own weights (R3)
                e["paper_w"] = {k: round(p["w"] * (0.5 if p["effect"] == "mixed" else 1), 3)
                                for k, p in per_paper.items()
                                if p["effect"] in ("positive", "na", "mixed")}
            e["papers"] = len(per_paper)
            e["negative_papers"] = len(neg)
            e["confidence"] = noisy_or(sup)
            e["mostly_negative"] = len(neg) > len(sup)
            levels = sorted({ev["level"] for ev in e["evidence"]},
                            key=lambda x: (-LEVEL_W.get(x, 0), x))
            e["level"] = levels[0] if levels else "review"

    # -- neighbourhoods ------------------------------------------------------------
    def _nbrs(self, nid: str) -> dict[str, list[dict]]:
        """neighbour -> [{rel, via, causal}] over mechanism edges of both graphs."""
        if nid in self._nb:
            return self._nb[nid]
        out: dict[str, list[dict]] = defaultdict(list)
        for (s, p, o), e in self.edges.items():
            if p in MECHANISM_PREDICATES and nid in (s, o):
                other = o if s == nid else s
                out[other].append({"rel": p, "via": "evidence", "causal": p == "causes",
                                   "edge": e})
        for other, rels in self.profile.adjacency.get(nid, {}).items():
            out[other].append({"rel": sorted(rels)[0], "via": "source_graph",
                               "causal": any(c in r for r in rels for c in CAUSAL),
                               "hierarchy": any(h in r for r in rels for h in HIERARCHY),
                               "child": any(r.startswith("(inverse)") for r in rels)})
        self._nb[nid] = out
        return out

    def kind(self, nid: str) -> str:
        k = self.nodes[nid]["kind"] if nid in self.nodes else self.profile.kinds.get(nid, "")
        return {"cell": "cell_type"}.get(k, k)

    def label(self, nid: str) -> str:
        return (self.nodes.get(nid) or {}).get("label") or self.profile.labels.get(nid, nid)

    def bridges(self, x: str) -> list[dict]:
        """Ways X connects to the input disease, best first: {weight, why, evidence}."""
        d = self.profile.id
        if x == d:
            return [{"weight": 1.0, "why": "is the input disease", "evidence": []}]
        if improvements.on("input_identity") and x in self.input_ids():
            return [{"weight": 1.0, "why": f"is the input disease (as {self.label(x)})",
                     "evidence": []}]
        out = []
        nd, nx_ = self.input_nbrs(), self._nbrs(x)
        kx = self.kind(x)
        if improvements.on("symptom_axis") and self.is_symptom(x):
            out.append({"weight": SYMPTOM_W, "why": f"typed input symptom {self.label(x)}",
                        "evidence": [], "basis": "symptom"})
        if kx == "disease":
            for r in nd.get(x, []):
                if r["rel"] == "shares_mechanism_with":
                    out.append({"weight": 0.9, "why": "shares mechanism with input disease",
                                "evidence": _quotes(r["edge"])})
                elif r.get("hierarchy"):
                    out.append({"weight": 0.6, "why": "ontology "
                                + ("subtype" if r["child"] else "parent") + " of input disease",
                                "evidence": [f"source graph: {r['rel']}"]})
            phen = []
            for m in set(nd) & set(nx_):
                km = self.kind(m)
                rd, rx = nd[m], nx_[m]
                ev = [q for r in rd + rx if r["via"] == "evidence" for q in _quotes(r["edge"])]
                if not ev:
                    ev = [f"source graph: {r['rel']}" for r in rd + rx][:2]
                if km == "phenotype":
                    phen.append((m, ev))
                elif km == "gene":
                    causal = any(r["causal"] for r in rd) and any(r["causal"] for r in rx)
                    out.append({"weight": 1.0 if causal else KIND_W["gene"],
                                "why": f"{'causal ' if causal else ''}gene {self.label(m)} "
                                       "in both", "evidence": ev})
                elif km in KIND_W:
                    out.append({"weight": KIND_W[km], "why": f"shared {km} {self.label(m)}",
                                "evidence": ev, "basis": km})
                elif km == "disease" and any(r.get("hierarchy") and not r["child"] for r in rd) \
                        and any(r.get("hierarchy") and not r["child"] for r in rx) \
                        and not m.startswith(GENERIC):
                    out.append({"weight": 0.4, "why": f"sibling under {self.label(m)}",
                                "evidence": [f"source graph: both subclass_of {m}"]})
            if phen:
                out.append({"weight": min(PHENOTYPE_MAX, PHENOTYPE_EACH * len(phen)),
                            "why": f"{len(phen)} shared phenotypes: "
                                   + ", ".join(self.label(m) for m, _ in phen[:6]),
                            "evidence": [q for _, ev in phen[:3] for q in ev[:1]],
                            "basis": "phenotype"})
        elif x in nd:  # a mechanism node linked to D
            rs = nd[x]
            if kx == "gene":
                w = 1.0 if any(r["causal"] for r in rs) else KIND_W["gene"]
            elif kx == "phenotype":
                w = 0.3
            else:
                w = KIND_W.get(kx, 0.3)
            ev = [q for r in rs if r["via"] == "evidence" for q in _quotes(r["edge"])] or \
                [f"source graph: {r['rel']}" for r in rs][:2]
            out.append({"weight": w, "why": f"{kx} {self.label(x)} is linked to the input "
                                            "disease", "evidence": ev, "basis": kx})
        out.sort(key=lambda b: -b["weight"])
        return out

    # -- where things were tested --------------------------------------------------
    def input_ids(self) -> set[str]:
        """The input disease and its ontology subtypes (source graph subclass_of);
        input_identity: also the input's xref ids and disease nodes named as a qualified
        form / the unqualified parent of an input name (qualified_match)."""
        d = self.profile.id
        base = {d} | {o for o, rels in self.profile.adjacency.get(d, {}).items()
                      if "(inverse) subclass_of" in rels}
        if not improvements.on("input_identity"):
            return base
        if self._ins is None:
            names = self.profile.disease.get("names") or [self.profile.disease["label"]]
            names = [self.profile.disease["label"], *names]
            xr = set(self.profile.disease.get("xrefs") or [])
            self._ins = {n["id"] for n in self.nodes.values() if n["kind"] == "disease"
                         and (n["id"] in xr or qualified_match(n["label"], names))}
        return base | self._ins

    def input_nbrs(self) -> dict[str, list[dict]]:
        """_nbrs of the input; input_identity: merged over all input_ids (minus them)."""
        d = self.profile.id
        if not improvements.on("input_identity"):
            return self._nbrs(d)
        if "\0input" in self._nb:
            return self._nb["\0input"]
        ins = self.input_ids()
        out: dict[str, list[dict]] = defaultdict(list)
        for i in sorted(ins, key=lambda i: (i != d, i)):
            for m, rs in self._nbrs(i).items():
                if m not in ins:
                    out[m] += rs
        self._nb["\0input"] = out
        return out

    def is_symptom(self, x: str) -> bool:
        """x is a phenotype the user typed (symptom mode): its HPO id, a phenotype /
        process / disease node of the same name (HPO label or the typed text), or a
        qualified form of it ("trauma-induced heterotopic ossification")."""
        sy = getattr(self.profile, "symptoms", None) or []
        if not sy:
            return False
        if any(x == s["id"] for s in sy):
            return True
        if self.kind(x) not in ("phenotype", "process", "disease"):
            return False
        names = [n for s in sy for n in s.get("names") or [s["label"]]]
        lab = self.label(x)
        return any(canon_key(lab) == canon_key(n) for n in names) or \
            qualified_match(lab, names) in ("same name", "qualified")

    def input_models(self) -> set[str]:
        """Model systems with an evidenced, not mostly negative "models" edge to D."""
        ins = self.input_ids()
        return {s for (s, p, o), e in self.edges.items()
                if p == "models" and o in ins and not e.get("mostly_negative")}

    def context(self, e: dict, ins: set[str], models: set[str]) -> dict | None:
        """The bridge an edge gets from where it was tested (D's patients or models)."""
        best = None
        for ev in e["evidence"]:
            if ev.get("context_disease") in ins and ev.get("context_model"):
                kind = "model"
                why = f"tested in {self.label(ev['context_model'])} (model of the input disease)"
            elif ev.get("context_disease") in ins:
                kind, why = "patients", "tested in the input disease (patients / patient cells)"
            elif ev.get("context_model") in models:
                kind = "model"
                why = f"tested in {self.label(ev['context_model'])} (model of the input disease)"
            elif e["to"] in models:
                kind, why = "model", f"tested in {self.label(e['to'])} (model of the input disease)"
            else:
                continue
            if best is None or CONTEXT_W[kind] > best["weight"]:
                best = {"weight": CONTEXT_W[kind], "why": why, "evidence": []}
            if len(best["evidence"]) < 3:
                best["evidence"].append(f"\"{ev['quote']}\" ({ev['paper']}, {ev['passage']})")
        return best

    def candidates(self) -> list[dict]:
        ins, models = self.input_ids(), self.input_models()
        by_sol: dict[str, list[dict]] = defaultdict(list)
        for (s, p, o), e in self.edges.items():
            if p not in SOLUTION_PREDICATES or self.kind(s) not in SOLUTION_TYPES:
                continue
            ctx = self.context(e, ins, models)
            bs = ([ctx] if ctx else []) + self.bridges(o)
            if not bs:
                continue
            b = max(bs, key=lambda x: x["weight"])
            neg_w = NEGATIVE_W if e["mostly_negative"] else 1
            score = e["confidence"] * b["weight"] * neg_w
            by_sol[s].append({"score": round(score, 3), "edge": f"{self.label(s)} {p} "
                              f"{self.label(o)}", "target": o, "predicate": p,
                              "confidence": e["confidence"], "papers": e["papers"],
                              "negative_papers": e["negative_papers"], "level": e["level"],
                              "bridge": b["why"], "bridge_weight": b["weight"],
                              "bridge_evidence": b["evidence"][:3],
                              "evidence": _quotes(e)[:3],
                              "direct": o in ins or ctx is not None,
                              "found_by": sorted({self.origins.get(ev["paper"], "core")
                                                  for ev in e["evidence"]})})
            # internal, removed below: per-paper path scores (R3), bridge basis (R4)
            by_sol[s][-1]["_paper"] = {k: w * b["weight"] * neg_w
                                       for k, w in (e.get("paper_w") or {}).items()}
            by_sol[s][-1]["_basis"] = "context" if b is ctx else b.get("basis")
        paper_scoring = improvements.on("paper_scoring")
        generic = improvements.on("generic_penalty")
        specific = set()
        if generic:
            for g in self.profile.genes:
                specific |= set(key(g["label"]).split())
            for n in self.profile.disease.get("names") or []:
                specific |= {w for w in key(_fold(n)).split() if len(w) > 3}
            specific -= {"disease", "syndrome", "disorder", "deficiency", "type"}
        out = []
        self.non_solutions = []
        for s, paths in by_sol.items():
            paths.sort(key=lambda x: -x["score"])
            extra = {}
            why = is_non_solution(self.label(s), self.kind(s)) if generic else ""
            if why:  # kept in the graph, not ranked
                self.non_solutions.append({"id": s, "label": self.label(s),
                                           "kind": self.kind(s), "non_solution": True,
                                           "why": why})
                continue
            if paper_scoring:
                best: dict[str, float] = {}
                for x in paths:
                    for k, v in x["_paper"].items():
                        best[k] = max(best.get(k, 0.0), v)
                tier, level = self._tier(s, paths, best)
                score = round(noisy_or(best.values()) * TIER_W[tier], 3)
                extra = {"n_papers": len(best), "best_level": level, "tier": tier}
            else:
                score = noisy_or(x["score"] for x in paths)
            category = "direct" if any(x["direct"] for x in paths) else "transfer"
            if generic and self.kind(s) not in INTERVENTION and is_generic(self.label(s),
                                                                            specific):
                score = round(score * GENERIC_W, 3)
                extra["generic"] = True
            if generic and category == "transfer" and self.kind(s) == "outcome_measure" and \
                    not any(x["_basis"] in ("phenotype", "process", "symptom") for x in paths):
                score = round(score * OTHER_ENDPOINT_W, 3)
                extra["other_disease_endpoint"] = True
            for x in paths:
                del x["_paper"], x["_basis"]
            if score <= 0:
                continue
            neg_direct = [x for x in paths if x["direct"] and x["negative_papers"]
                          and x["confidence"] == 0]
            out.append({"id": s, "label": self.label(s), "kind": self.kind(s),
                        "score": score,
                        "category": category,
                        "tested_without_benefit_in_input": bool(neg_direct)
                        and self.kind(s) in ("drug", "therapy"),
                        "found_by": sorted({o for x in paths for o in x["found_by"]}),
                        "papers": sorted({ev["paper"] for x in paths
                                          for ev in self.edges_for(s, x)}),
                        **extra,
                        "paths": paths[:8]})
        out.sort(key=lambda c: -c["score"])
        return out

    def _tier(self, s: str, paths: list[dict], support: dict[str, float]) -> tuple[int, str]:
        """(evidence tier, best level) of a candidate from its supporting papers' levels:
        3 clinical trial / registered trial, 2 two or more primary papers or human
        observational data, 1 otherwise (one preclinical paper, reviews, databases)."""
        level_of: dict[str, str] = {}
        for x in paths:
            for ev in self.edges_for(s, x):
                if ev["paper"] in support and ev["effect"] not in ("negative", "null"):
                    cur = level_of.get(ev["paper"])
                    if cur is None or LEVEL_W.get(ev["level"], 0) > LEVEL_W.get(cur, 0):
                        level_of[ev["paper"]] = ev["level"]
        levels = set(level_of.values())
        best = max(levels, key=lambda lv: (LEVEL_W.get(lv, 0), lv)) if levels else ""
        primary = [p for p, lv in level_of.items() if lv not in (*SECONDHAND, "database")]
        # treats / tested_in the input: registry rows (trials, Open Targets) and approval
        # count as clinical; two independent papers saying so (even reviews) as replicated
        on_input = [ev for x in paths
                    if x["direct"] and x["predicate"] in ("treats", "tested_in")
                    for ev in self.edges_for(s, x)
                    if ev["paper"] in support and ev["effect"] not in ("negative", "null")]
        if levels & set(CLINICAL) or any(self.origins.get(ev["paper"]) == "registry"
                                         or APPROVED_RX.search(ev["quote"]) for ev in on_input):
            return 3, best
        if len(primary) >= 2 or levels & set(HUMAN) or \
                len({ev["paper"] for ev in on_input}) >= 2:
            return 2, best
        return 1, best

    # -- mechanisms established in the input disease ---------------------------------
    def mechanisms(self, kinds=("gene", "pathway", "process", "cell_type"),
                   limit: int = 30) -> list[dict]:
        """Mechanism nodes M established in D: an evidence edge between M and D (or one of
        D's models), a solution edge onto M obtained in D / its models, or a causal gene
        of the source graph. Best supported first:
        {id, label, kind, papers, confidence, evidence}."""
        ins, models = self.input_ids(), self.input_models()
        here = ins | models
        found: dict[str, dict] = {}
        for (s, p, o), e in self.edges.items():
            if e.get("mostly_negative") or not e.get("confidence"):
                continue
            ms = []
            if s in here and o not in here:
                ms.append(o)
            if o in here and s not in here:
                ms.append(s)
            if p in SOLUTION_PREDICATES and o not in here and self.context(e, ins, models):
                ms.append(o)
            for m in ms:
                if self.kind(m) not in kinds:
                    continue
                f = found.setdefault(m, {"id": m, "label": self.label(m), "kind": self.kind(m),
                                         "papers": set(), "confidence": [], "evidence": []})
                f["papers"] |= {ev["paper"] for ev in e["evidence"]
                                if self.origins.get(ev["paper"]) != "registry"}
                f["confidence"].append(e["confidence"])
                if len(f["evidence"]) < 3:
                    f["evidence"] += _quotes(e)[:1]
        for m, rels in self.profile.adjacency.get(self.profile.id, {}).items():
            if m not in found and self.kind(m) in kinds and \
                    any(c in r for r in rels for c in CAUSAL):
                found[m] = {"id": m, "label": self.label(m), "kind": self.kind(m),
                            "papers": set(), "confidence": [1.0],
                            "evidence": [f"source graph: {sorted(rels)[0]}"]}
        out = []
        for f in found.values():
            f["confidence"] = noisy_or(f["confidence"])
            f["papers"] = sorted(f["papers"])
            out.append(f)
        out.sort(key=lambda f: (-len(f["papers"]), -f["confidence"], f["label"]))
        return out[:limit]

    def edges_for(self, s, path) -> list[dict]:
        e = self.edges.get((s, path["predicate"], path["target"]))
        return e["evidence"] if e else []


def _quotes(e: dict) -> list[str]:
    out, seen = [], set()
    for ev in e["evidence"]:
        if ev["paper"] in seen:
            continue
        seen.add(ev["paper"])
        out.append(f"\"{ev['quote']}\" ({ev['paper']}, {ev['passage']})")
    return out
