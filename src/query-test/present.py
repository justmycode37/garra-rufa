#!/usr/bin/env python3
"""Patient-facing view of a graph from main.py: only what helps someone who has (or cares
for someone with) a specific rare disease learn more about it.

Input is the graph JSON of main.py (-o run.json). The disease the query was about (its
"focus") becomes the centre; its direct neighbours are kept when they are useful to a
patient and sorted into sections, and within a section into groups that become one node
each (the items are listed in the node and can be expanded in the viewer):

  Symptoms        grouped by body system (top-level HPO branches), most frequent first;
                  frequencies from the HPO annotations. --subtype-symptoms (default on): a
                  disease group without annotations of its own ("Batten disease" =
                  MONDO's "juvenile neuronal ceroid lipofuscinosis") shows the symptoms
                  its subtypes share ("in 5 of 8 subtypes"); --no-subtype-symptoms: none
  Genetics        inheritance, disease-causing genes (with their ClinVar variant counts;
                  variants never become nodes), other associated genes
  Related         the most specific broader disease groups, subtypes, closely related
                  conditions and diseases with similar symptoms (HPO annotation
                  similarity, computed here)
  Treatments      approved / indicated drugs, drugs tested in trials, cautions
  Clinical trials grouped by status (recruiting first)
  Patient support patient organisations, grouped by country
  Expert care     European Reference Networks, expert centres (by country), networks
  Research        research projects, patient registries, biobanks

Entries with the same name in one group merge (the Orphanet and EURORDIS copies of an
organisation, the sites of one reference centre); countries / body systems with few
entries merge into "Other ...".

Dropped: identifiers / cross-references, variants as nodes, model-organism and molecular
detail (GO terms, pathways, cells, tissues, eQTLs, protein interactions), sponsors,
drug side effects, therapeutic areas and generic groups ("autosomal dominant disease"),
onset / clinical-modifier terms (onset becomes a fact about the disease), labels that are
only an id, and research entries listed only for a broad disease group that shares no
word with this disease ("national registry of rare diseases").

Connections between kept items (not just item -> disease) come from:
  graph     edges main.py found between two kept items (2nd iteration)
  primekg   the local PrimeKG index: drug -> gene targets, gene/disease -> phenotype,
            drug -> disease indications, gene -> gene interactions, ...
  hpo       which of the disease's symptoms each related disease also has
  text      a trial / project / registry whose title or summary names a kept drug,
            gene, disease or symptom
In the viewer, connections of collapsed groups are drawn between the groups.

Symptom / gene searches (main.py "FBN1", "tall stature, arachnodactyly", ...) have several
candidate diseases: then a Markdown overview is written (how the input was read, the
ranked candidates and why, the symptoms that tell the top candidates apart) together with
one profile per top candidate (<out>.<rank>-<name>.<suffix>, linked from the overview).
The searched symptoms and genes are marked in each profile.

Usage:
  python src/query-test/present.py runs/marfan.json                 # -> runs/marfan.present.html
  python src/query-test/present.py runs/fbn1.json -o runs/fbn1.present.md  # overview + profiles
  python src/query-test/present.py runs/pmm2.json -o runs/pmm2.present.md
  python src/query-test/present.py runs/sym.json --focus MONDO:0009352 -o sym.present.json
                                                                    # one candidate only
"""
import argparse
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from entities import normalize  # noqa: E402
from sources import _hpoa  # noqa: E402
from sources import _local  # noqa: E402,F401  (puts <repo>/src on sys.path: garra.local)
from sources.base import words  # noqa: E402

PRIMEKG_DB = Path(__file__).resolve().parents[2] / "data" / "primekg" / "primekg.sqlite"
CURIE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*:\S+$")
ID_LABEL = re.compile(r"^(?:[A-Za-z][A-Za-z0-9_.-]*:\S+|[A-Z]+_\d+)$")  # "HP:0005113", "HP_0005113"

SECTIONS = [  # id, label, colour (viewer)
    ("symptoms", "Symptoms & signs", "#e15759"),
    ("genetics", "Genetics", "#4e79a7"),
    ("related", "Related conditions", "#b07aa1"),
    ("treatment", "Treatments", "#59a14f"),
    ("trials", "Clinical trials", "#f28e2b"),
    ("support", "Patient support", "#edc948"),
    ("care", "Expert care", "#76b7b2"),
    ("research", "Research", "#9c755f"),
]
SECTION_ORDER = {s: i for i, (s, _, _) in enumerate(SECTIONS)}

# friendly names of the top-level HPO branches (children of "Phenotypic abnormality");
# a branch missing here is named from its label ("Abnormality of the X" -> "X")
BODY_SYSTEMS = {
    "HP:0000119": "Kidneys, urinary tract & genitals", "HP:0000152": "Head & neck",
    "HP:0000478": "Eyes & vision", "HP:0000598": "Ears & hearing",
    "HP:0000707": "Brain & nervous system", "HP:0000769": "Breast",
    "HP:0000818": "Hormones (endocrine)", "HP:0001197": "Pregnancy & birth",
    "HP:0001507": "Growth", "HP:0001574": "Skin, hair & nails", "HP:0001608": "Voice",
    "HP:0001626": "Heart & blood vessels", "HP:0001871": "Blood & immunity cells",
    "HP:0001939": "Metabolism", "HP:0002086": "Lungs & breathing", "HP:0002664": "Tumours",
    "HP:0002715": "Immune system", "HP:0025031": "Digestive system",
    "HP:0025142": "General symptoms", "HP:0025354": "Cell-level findings",
    "HP:0033127": "Bones, joints & muscles", "HP:0040064": "Arms, legs, hands & feet",
    "HP:0045027": "Chest",
}
FREQ_LABEL = [(0.99, "always"), (0.8, "very frequent (80-99%)"), (0.3, "frequent (30-79%)"),
              (0.05, "occasional (5-29%)"), (0.0, "rare (<5%)")]

ALIAS_RELATIONS = {"xref", "xref_broader", "xref_narrower", "matches", "mesh_heading",
                   "same_as", "exact_match"}
# one record per disease (as entities.LISTED): a node listing such an id is that record
LISTED_PREFIXES = {"NORD", "GARD"}
CAUSAL_GENE_RELATIONS = {"caused_by_gene", "causes_disease", "disease_has_basis_in",
                         "has_material_basis_in", "gene_associated_disease"}
# sources whose disease-gene links are curated (OpenTargets / PrimeKG alone are not)
CURATED_GENE_SOURCES = {"hpo", "orphadata", "mondo", "monarch", "raresource", "clinvar",
                        "hpoa"}
KIND_ORDER = {"drug": 0, "gene": 1, "disease": 2, "phenotype": 3}
DROP_KINDS = {"anatomy", "cell", "cell_line", "function", "process", "component", "pathway",
              "term", "query", "exposure", "unknown"}

COUNTRY_SET = {
    "Albania", "Algeria", "Andorra", "Argentina", "Armenia", "Australia", "Austria",
    "Azerbaijan", "Bahrain", "Belarus", "Belgium", "Bolivia", "Bosnia and Herzegovina",
    "Brazil", "Bulgaria", "Canada", "Chile", "China", "Colombia", "Costa Rica", "Croatia",
    "Cuba", "Cyprus", "Czech Republic", "Czechia", "Denmark", "Ecuador", "Egypt", "Estonia",
    "Ethiopia", "Finland", "France", "Georgia", "Germany", "Ghana", "Greece", "Hong Kong",
    "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland", "Israel", "Italy",
    "Japan", "Jordan", "Kazakhstan", "Kenya", "Korea", "Kosovo", "Kuwait", "Latvia",
    "Lebanon", "Liechtenstein", "Lithuania", "Luxembourg", "Malaysia", "Malta", "Mexico",
    "Moldova", "Monaco", "Montenegro", "Morocco", "Nepal", "Netherlands", "New Zealand",
    "Nigeria", "North Macedonia", "Norway", "Oman", "Pakistan", "Panama", "Paraguay", "Peru",
    "Philippines", "Poland", "Portugal", "Qatar", "Romania", "Russia", "Russian Federation",
    "San Marino", "Saudi Arabia", "Serbia", "Singapore", "Slovakia", "Slovenia",
    "South Africa", "South Korea", "Spain", "Sri Lanka", "Sweden", "Switzerland", "Taiwan",
    "Thailand", "Tunisia", "Turkey", "Türkiye", "Ukraine", "United Arab Emirates",
    "United Kingdom", "UK", "United States", "USA", "Uruguay", "Venezuela", "Vietnam",
}
COUNTRY_ALIASES = {"UK": "United Kingdom", "USA": "United States", "Czechia": "Czech Republic",
                   "Russian Federation": "Russia", "Türkiye": "Turkey",
                   "South Korea": "Korea"}
# directories that only list organisations of one country (NORD lists international ones)
SOURCE_COUNTRY = {"genetic_alliance_uk": "United Kingdom"}
TRIAL_STATUS = [  # (status words, group label)
    (("recruiting", "not yet recruiting", "enrolling by invitation", "available"),
     "Open / starting soon"),
    (("active not recruiting",), "Running (not recruiting)"),
    (("completed", "approved for marketing"), "Completed"),
    (("terminated", "withdrawn", "suspended", "no longer available", "temporarily not available"),
     "Stopped early"),
]
MAX_GROUPS = {"symptoms": 10}  # groups per section; the smallest merge into "Other ..."
DEFAULT_MAX_GROUPS = 6
MAX_BROADER = 6  # broader disease groups kept (most specific first)
ONSET_ROOT = "HP:0003674"  # "Onset": shown as a fact about the disease, not as a symptom
# disease "groups" that say nothing beyond the inheritance mode / being rare or genetic
GENERIC_DISEASE = re.compile(
    r"^((autosomal|x linked|y linked|mitochondrial|dominant|recessive|inherited|hereditary|"
    r"genetic|rare|syndromic|human|congenital|monogenic)\s+)+(disease|disorder|syndrome)s?$")
URL_PATTERNS = {
    "ORPHA": ("Orphanet", "https://www.orpha.net/en/disease/detail/{}"),
    "OMIM": ("OMIM", "https://omim.org/entry/{}"),
    "GARD": ("GARD (NIH)", "https://rarediseases.info.nih.gov/diseases/{}"),
    "MONDO": ("Monarch", "https://monarchinitiative.org/MONDO:{}"),
    "HP": ("HPO", "https://hpo.jax.org/browse/term/HP:{}"),
    "HGNC": ("HGNC", "https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/HGNC:{}"),
    "NCT": ("ClinicalTrials.gov", "https://clinicaltrials.gov/study/{}"),
    "DrugBank": ("DrugBank", "https://go.drugbank.com/drugs/{}"),
    "ChEMBL": ("ChEMBL", "https://www.ebi.ac.uk/chembl/compound_report_card/{}"),
}
SOURCE_NAMES = {"orphadata": "Orphanet", "orphanet_groups": "Orphanet", "nord": "NORD",
                "raresource": "GARD (NIH)", "mondo": "MONDO", "monarch": "Monarch",
                "hpo": "HPO", "opentargets": "Open Targets", "disease_ontology": "Disease Ontology",
                "eurordis": "EURORDIS", "clinicaltrials": "ClinicalTrials.gov",
                "clinvar": "ClinVar", "genetic_alliance_uk": "Genetic Alliance UK",
                "genetic_alliance_us": "Genetic Alliance US", "ern": "ERN", "primekg": "PrimeKG",
                "rdcrn": "RDCRN", "mesh": "MeSH"}
DESCRIPTION_PREFERENCE = ["orphadata", "raresource", "nord", "mondo", "monarch",
                          "disease_ontology", "opentargets", "mesh", "clinicaltrials"]
PRIMEKG_RELATIONS = {  # PrimeKG relation (display name) -> connection label
    "associated with": "associated with", "phenotype present": "has symptom",
    "indication": "treats", "contraindication": "should be avoided in",
    "off-label use": "used off-label for", "target": "acts on", "enzyme": "metabolised by",
    "transporter": "transported by", "carrier": "carried by", "ppi": "protein interacts with",
    "synergistic interaction": None, "parent-child": "related to",
    "side effect": None, "expression present": None, "expression absent": None,
    "phenotype absent": None, "interacts with": None, "linked to": None,
}


# -- helpers ---------------------------------------------------------------------------
def node_links(nid: str, n: dict) -> list[dict]:
    """Source pages of a node: its urls, then pages built from its ids."""
    out, seen = [], set()
    urls = (n.get("info") or {}).get("urls") or {}
    for src, url in urls.items():
        if isinstance(url, str) and url not in seen:
            seen.add(url)
            out.append({"label": SOURCE_NAMES.get(src, src), "url": url})
    names = {x["label"] for x in out}
    for cid in (nid, *n.get("xrefs", ())):
        p = _prefix(cid)
        if p in URL_PATTERNS and URL_PATTERNS[p][0] not in names:
            lab, pat = URL_PATTERNS[p]
            out.append({"label": lab, "url": pat.format(_local(cid))})
            names.add(lab)
    for url in (n.get("info") or {}).get("links") or ():
        if isinstance(url, str) and url.startswith("http") and url not in seen:
            seen.add(url)
            out.append({"label": re.sub(r"^https?://(www\.)?([^/]+).*", r"\2", url),
                        "url": url})
    return out[:6]


def _prefix(curie: str) -> str:
    return curie.split(":", 1)[0]


def _local(curie: str) -> str:
    return curie.split(":", 1)[1] if ":" in curie else curie


def _norm_label(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "x"


def _ontology():
    """garra.local's ontology index (MONDO is_a, xrefs), or None without it."""
    try:
        from garra.local import available, ontology
    except Exception:
        return None
    return ontology if available("ontology") else None


def freq_label(f: float | None) -> str | None:
    if f is None:
        return None
    return next(lab for lo, lab in FREQ_LABEL if f >= lo)


def split_country(label: str) -> tuple[str, str | None]:
    """'Marfan Trust (United Kingdom)' -> ('Marfan Trust', 'United Kingdom')."""
    m = re.match(r"^(.*\S)\s*\(([^()]+)\)\s*$", label)
    if m and m[2].strip() in COUNTRY_SET:
        c = m[2].strip()
        return m[1], COUNTRY_ALIASES.get(c, c)
    return label, None


def humanize(rel: str) -> str:
    rel = rel.removeprefix("(inverse) ")
    for a, b in (("trial_drug_phase_", "tested in phase "), ("_", " ")):
        rel = rel.replace(a, b)
    return rel


class Graph:
    """The main.py graph JSON with adjacency in both directions."""

    def __init__(self, data: dict):
        self.data = data
        self.nodes: dict[str, dict] = {n["id"]: n for n in data["nodes"]}
        self.adj: dict[str, list[tuple[str, str, str, bool]]] = defaultdict(list)
        for e in data["edges"]:
            if e["from"] in self.nodes and e["to"] in self.nodes:
                self.adj[e["from"]].append((e["to"], e["relation"], e["source"], True))
                self.adj[e["to"]].append((e["from"], e["relation"], e["source"], False))

    def ids(self, nid: str) -> set[str]:
        n = self.nodes[nid]
        return {nid, *n.get("xrefs", ())}

    def pick_focus(self, wanted: str | None) -> str | None:
        if wanted:
            want = normalize(wanted)
            for nid in self.nodes:
                if want == nid or want in {normalize(x) for x in self.ids(nid)}:
                    return nid
            low = wanted.strip().lower()
            for nid, n in self.nodes.items():
                if n["label"].strip().lower() == low:
                    return nid
            return None
        for f in self.data.get("focus") or ():
            if f in self.nodes:
                return self._richer(f)
        # fallback: the disease the most sources matched to the input
        start = self.data.get("start")
        votes = Counter(o for o, rel, _, out in self.adj.get(start, ())
                        if out and self.nodes[o]["kind"] == "disease")
        if votes:
            return votes.most_common(1)[0][0]
        diseases = [n for n in self.nodes if self.nodes[n]["kind"] == "disease"]
        return max(diseases, key=lambda n: len(self.adj[n]), default=None)


    def listing(self, nid: str) -> list[str]:
        """Disease nodes that are the same record as `nid` by a NORD / GARD xref (one
        lists the other's id), left unmerged by main.py."""
        mine = {x for x in self.ids(nid) if _prefix(x) in LISTED_PREFIXES}
        out = []
        for o, n in self.nodes.items():
            if o == nid or n["kind"] != "disease":
                continue
            theirs = {x for x in self.ids(o) if _prefix(x) in LISTED_PREFIXES}
            if o in mine or nid in theirs:
                out.append(o)
        return out

    def _richer(self, f: str) -> str:
        """A focus that is a bare directory record (NORD / GARD: one source, no xrefs,
        e.g. NORD's "Pompe Disease" picked for its label) gives an empty profile: use
        the disease that lists it as xref, else the best-connected disease the input's
        name search matched."""
        n = self.nodes[f]
        if n.get("xrefs") or len(set(n.get("sources") or ()) - {"input"}) > 1 or                _prefix(f) not in LISTED_PREFIXES:
            return f
        same = self.listing(f)
        if same:
            return max(same, key=lambda o: len(self.adj[o]))
        start = self.data.get("start")
        hits = [o for o, rel, _, out in self.adj.get(start, ())
                if out and rel == "matches" and self.nodes[o]["kind"] == "disease"]
        best = max(hits, key=lambda o: len(self.adj[o]), default=f)
        return best if len(self.adj[best]) > 2 * len(self.adj[f]) else f


# -- curated view ----------------------------------------------------------------------
class Present:
    def __init__(self, g: Graph, focus: str, similar: int = 8, subtype_symptoms: bool = True):
        self.g = g
        self.focus = focus
        self.similar_n = similar
        self.hpo = _hpoa.load()
        self.items: dict[str, dict] = {}
        self.links: dict[tuple[str, str, str], dict] = {}
        self.notes: list[str] = []
        f = g.nodes[focus]
        self.focus_words = words(f["label"])
        # the focus' own vocabulary (label, synonyms): tells broader groups from relatives
        self.vocab = set().union(self.focus_words, *(
            words(x) for x in (f.get("info") or {}).get("synonyms") or ()))
        self.onset: list[str] = []
        self.aliases = self._aliases()
        self.focus_ids = set().union(*(g.ids(a) for a in self.aliases))
        self.hpo_ids = [i for i in sorted(self.focus_ids)
                        if self.hpo and normalize(i) in self.hpo.ann]
        self.freq = self._frequencies()
        # subtype_symptoms: subtype -> its HPO-annotated ids; HP -> share of subtypes with it
        self.subtypes: dict[str, list[str]] = {}
        self.subtype_share: dict[str, float] = {}
        if subtype_symptoms and self.hpo and not self.hpo_ids:
            self._subtype_annotations()

    # which nodes are the focus disease under another id (unmerged xrefs / name matches)
    def _aliases(self) -> set[str]:
        out = {self.focus}
        name = _norm_label(self.g.nodes[self.focus]["label"])
        for o, rel, _, _ in self.g.adj[self.focus]:
            n = self.g.nodes[o]
            if rel in ALIAS_RELATIONS and n["kind"] == "disease" and _norm_label(n["label"]) == name:
                out.add(o)
        out.update(self.g.listing(self.focus))  # unmerged NORD / GARD copy of the focus
        start = self.g.data.get("start")
        if start in self.g.nodes and self.g.nodes[start]["kind"] == "term":
            out.add(start)  # free-text input: name-searched hits hang off it
        return out

    def _frequencies(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for d in self.hpo_ids:
            for hp, f in self.hpo.ann[normalize(d)].items():
                out[hp] = max(out.get(hp, 0.0), f)
        return out

    def _subtype_annotations(self):
        """A focus without HPO annotations of its own that is a group of diseases gets
        the symptoms of its subtypes. Subtypes: the graph's has_subclass neighbours plus
        the MONDO children (local ontology). A subtype's annotated ids are its own
        OMIM / ORPHA ids, else those of its other MONDO parents: "juvenile neuronal
        ceroid lipofuscinosis 3" has no annotation, but is also a "neuronal ceroid
        lipofuscinosis 3" (OMIM:204200)."""
        h, onto = self.hpo, _ontology()
        subs = {o for a in self.aliases for o, rel, _, out in self.g.adj[a]
                if self.g.nodes[o]["kind"] == "disease"
                and ((rel == "has_subclass" and out) or (rel == "subclass_of" and not out))}
        mondo = {i for i in self.focus_ids if _prefix(i) == "MONDO"}
        if onto:
            for m in sorted(mondo):
                subs.update(onto.children(m))
        own = {normalize(i) for i in self.focus_ids} | mondo

        def annotated(ids):
            return {normalize(i) for i in ids if normalize(i) in h.ann} - own

        seen: set[frozenset] = set()  # one subtype under two ids (graph ORPHA, MONDO child)
        for s in sorted(subs):
            ids = set(self.g.ids(s)) if s in self.g.nodes else {s}
            if onto:
                ids |= {x for i in list(ids) if _prefix(i) == "MONDO" for x in onto.xrefs(i)}
            ann = annotated(ids)
            if not ann and onto and _prefix(s) == "MONDO":
                for p in onto.parents(s):
                    if p not in own:
                        ann |= annotated([p, *onto.xrefs(p)])
            if ann and frozenset(ann) not in seen:
                seen.add(frozenset(ann))
                self.subtypes[s] = sorted(ann)
        if len(self.subtypes) < 2:
            self.subtypes = {}
            return
        count: Counter = Counter()
        for ann in self.subtypes.values():
            count.update({hp for d in ann for hp in h.ann[d]})
        n = len(self.subtypes)
        self.subtype_share = {hp: c / n for hp, c in count.items()}
        self.notes.append(f"no HPO annotation of its own: symptoms combined from {n} subtypes "
                          f"({', '.join(sorted(self.subtypes))})")

    def _add_subtype_symptoms(self, top: int = 60):
        """subtype_symptoms: the symptoms at least a third of the subtypes share (at
        least two), most widely shared first."""
        if not self.subtype_share:
            return
        n = len(self.subtypes)
        need = max(2, math.ceil(n / 3))
        ranked = sorted((hp for hp, s in self.subtype_share.items() if s * n >= need - 1e-9),
                        key=lambda hp: (-self.subtype_share[hp], -self.hpo.specificity(hp), hp))
        added = 0
        for hp in ranked:
            note = f"in {round(self.subtype_share[hp] * n)} of {n} subtypes (HPO annotations)"
            if hp in self.items:
                self.items[hp]["notes"].append(note)
                self.items[hp]["score"] += self.subtype_share[hp]
                continue
            if added >= top or hp not in self.hpo.name:
                continue
            node = self.g.nodes.get(hp) or {"id": hp, "label": self.hpo.name[hp],
                                            "kind": "phenotype", "sources": ["hpo"], "info": {}}
            rels = [("has_phenotype", "hpo", True)]
            placed = self._classify(hp, node, rels)
            if not placed:
                continue  # onset / clinical modifier
            self._add_item(hp, node, placed[0], placed[1], rels, placed[2])
            if placed[0] == "symptoms":
                self.items[hp]["notes"].append(note)
                self.items[hp]["score"] += self.subtype_share[hp]
                added += 1

    # -- 1. classify direct neighbours ---------------------------------------------------
    def collect(self):
        neigh: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
        for a in self.aliases:
            for o, rel, src, out in self.g.adj[a]:
                if o in self.aliases or rel in ALIAS_RELATIONS:
                    continue
                if a != self.focus and rel in ("candidate_disease", "has_symptom"):
                    continue
                neigh[o].append((rel, src, out))
        symptom_query = self.g.nodes.get(self.g.data.get("start"), {}).get("kind") == "query"
        searched: dict[str, str] = {}
        if symptom_query:
            searched = {o: ("one of the searched symptoms" if rel == "has_symptom"
                            else "the searched gene")
                        for o, rel, _, out in self.g.adj[self.g.data["start"]]
                        if out and rel in ("has_symptom", "has_gene")}
            for o, rel, src, out in self.g.adj[self.g.data["start"]]:
                if rel == "candidate_disease" and o not in self.aliases:
                    neigh[o].append(("candidate_disease", src, True))
        variants = []
        for nid, rels in neigh.items():
            n = self.g.nodes[nid]
            if n["kind"] == "variant":
                variants.append((nid, rels))
                continue
            placed = self._classify(nid, n, rels)
            if placed:
                section, group, extra = placed
                self._add_item(nid, n, section, group, rels, extra)
                if nid in searched:
                    self.items[nid]["notes"].insert(0, searched[nid])
                    self.items[nid]["score"] += 1
        self._attach_variants(variants)
        self._add_subtype_symptoms()

    def _classify(self, nid: str, n: dict, rels) -> tuple[str, str, dict] | None:
        kind, names = n["kind"], {r for r, _, _ in rels}
        srcs = {s for _, s, _ in rels}
        if kind in DROP_KINDS or (ID_LABEL.match(n["label"]) and kind != "variant"):
            return None
        if n["label"].strip().title() in COUNTRY_SET or n["label"].strip() in COUNTRY_SET:
            return None  # e.g. an Orphanet trial-network entry named "BELGIUM"
        if kind == "phenotype":
            if names <= {"lacks_phenotype", "phenotype_absent_in", "disease_has_basis_in",
                         "has_material_basis_in", "side_effect"}:
                return None
            if names & {"has_inheritance"} or self._is_inheritance(nid, n):
                return "genetics", "Inheritance", {}
            if self.hpo and nid in self.hpo.name and\
                    _hpoa.PHENOTYPE_ROOT not in self.hpo.ancestors(nid):
                if ONSET_ROOT in self.hpo.ancestors(nid):  # "Congenital onset", ...
                    self.onset.append(n["label"])
                return None  # clinical modifiers / course: not a symptom
            return "symptoms", "", {}  # body system decided in _group_symptoms
        if kind == "gene":
            causal = bool(names & CAUSAL_GENE_RELATIONS) or bool(srcs & CURATED_GENE_SOURCES)
            if causal:
                return "genetics", "Disease-causing genes", {"causal": True}
            if len(srcs) >= 2:
                return "genetics", "Other associated genes", {}
            return None
        if kind == "disease":
            return self._classify_disease(nid, n, rels)
        if kind == "drug":
            if names & {"treated_by", "indicated_for", "approved_drug"}:
                return "treatment", "Approved / indicated", {}
            phases = [r for r in names if r.startswith("trial_drug_phase_")]
            if phases:
                best = max(int(re.sub(r"\D", "", p.removeprefix("trial_drug_phase_"))[:1] or 0)
                           for p in phases)
                return "treatment", "Tested in clinical trials", {"phase": best}
            if names & {"off_label_drug", "off_label_use"}:
                return "treatment", "Used off-label", {}
            if names & {"contraindicated_drug", "contraindicated_for"}:
                return "treatment", "Use with caution", {}
            return None
        if kind == "clinical_trial":
            status = (n["info"].get("status") or "").lower()
            if not status:
                m = re.search(r"\[([^\]]+)\]\s*$", n["label"])
                status = m[1].lower() if m else ""
            for keys, label in TRIAL_STATUS:
                if status in keys:
                    return "trials", label, {"status": status}
            return "trials", "Other / status unknown", {"status": status}
        if kind == "patient_organisation":
            return "support", "", {}  # by country
        if kind == "expert_centre":
            return "care", "", {}  # by country
        if kind == "ern":
            return "care", "European Reference Networks", {}
        if kind == "expert_network":
            return "care", "Expert & research networks", {}
        if kind == "research_project":
            return "research", "Research projects", {}
        if kind == "registry" or (kind == "organisation" and any("registry" in r for r in names)):
            return "research", "Patient registries", {}
        if kind == "biobank":
            return "research", "Biobanks", {}
        return None  # sponsors, unknown kinds

    def _classify_disease(self, nid, n, rels):
        label = _norm_label(n["label"])
        if GENERIC_DISEASE.match(label):
            return None
        broader = narrower = related = candidate = False
        for rel, src, out in rels:
            if rel == "candidate_disease":
                candidate = True
            elif rel == "in_therapeutic_area":
                continue
            elif (rel == "subclass_of" and out) or (rel == "has_subclass" and not out):
                broader = True
            elif (rel == "has_subclass" and out) or (rel == "subclass_of" and not out):
                narrower = True
            else:
                related = True
        if candidate:
            return "related", "Other candidate diagnoses", {}
        if narrower and not broader:
            return "related", "Subtypes", {}
        if broader and not narrower:
            return "related", "Broader disease groups", {}
        if related or (broader and narrower):
            # PrimeKG's parent-child is undirected: a label containing the focus name is
            # usually a subtype ("neonatal Marfan syndrome"), one made only of the focus'
            # words a broader group ("congenital disorder of glycosylation" for PMM2-CDG)
            w = words(label)
            if self.focus_words and self.focus_words <= w and len(w) > len(self.focus_words):
                return "related", "Subtypes", {}
            if w and w <= self.vocab:
                return "related", "Broader disease groups", {}
            return "related", "Closely related conditions", {}
        return None  # therapeutic areas only

    def _is_inheritance(self, nid, n) -> bool:
        if not self.hpo:
            return False
        hps = [x for x in (nid, *n.get("xrefs", ())) if _prefix(x) == "HP"]
        return any(self.hpo.is_inheritance(h) for h in hps)

    def _add_item(self, nid, n, section, group, rels, extra):
        info = n.get("info") or {}
        srcs = sorted({s for _, s, _ in rels} | set(n.get("sources") or ()) - {"input"})
        label = n["label"]
        notes = []
        names = {r for r, _, _ in rels}
        if all(r.endswith("_for_group") for r in names):
            notes.append("listed for a broader group of diseases that includes this one")
        elif all(r.endswith("_for_subtype") for r in names):
            notes.append("listed for a subtype of this disease")
        item = {"id": nid, "label": label, "kind": n["kind"], "section": section,
                "group": group, "notes": notes, "sources": [SOURCE_NAMES.get(s, s) for s in srcs],
                "links": self._links(nid, n), "score": len(srcs) * 0.1}
        desc = _description(info)
        if desc:
            item["description"] = desc
        if n["kind"] == "phenotype":
            f = self.freq.get(nid)
            if f is not None:
                item["frequency"] = freq_label(f)
                item["freq"] = round(f, 2)
                item["score"] += f
            elif self.hpo_ids:
                item["score"] -= 0.3  # not in the curated HPO annotation of the disease
            if self.hpo and nid in self.hpo.name:
                item["score"] += 0.3 * self.hpo.specificity(nid)
        elif group == "Broader disease groups":
            # Orphanet's groups are made for patients / clinicians; longer names are narrower
            item["score"] += 0.5 * ("orphadata" in srcs) + 0.1 * len(words(label))
        elif n["kind"] == "gene":
            if info.get("full_name"):
                item["description"] = info["full_name"]
            item["score"] += 2 * bool(extra.get("causal"))
        elif n["kind"] == "clinical_trial":
            item["label"] = re.sub(r"\s*\[[^\]]+\]\s*$", "", label)
            if extra.get("status"):
                item["status"] = extra["status"]
            sponsors = [self.g.nodes[o]["label"] for o, rel, _, out in self.g.adj[nid]
                        if out and rel in ("sponsored_by", "collaborator")]
            if sponsors:
                item["sponsors"] = sponsors[:3]
            item["score"] += {"Open / starting soon": 3, "Running (not recruiting)": 2,
                              "Completed": 1}.get(group, 0)
        elif n["kind"] == "drug":
            item["label"] = label[:1].upper() + label[1:].lower() if label.isupper() else label
            if extra.get("phase"):
                item["phase"] = extra["phase"]
                item["score"] += extra["phase"] * 0.2
        elif section in ("support", "care") and not group:
            item["label"], country = split_country(label)
            if not country:
                country = next((SOURCE_COUNTRY[s] for s in srcs if s in SOURCE_COUNTRY), None)
            item["country"] = country or "International / not stated"
        if notes:
            item["score"] -= 0.5
        self.items[nid] = item

    def _links(self, nid, n) -> list[dict]:
        return node_links(nid, n)

    def _attach_variants(self, variants):
        """Variants become counts on the gene items ("12 disease-causing variants")."""
        if not variants:
            return
        genes = {it["label"].upper(): it for it in self.items.values()
                 if it["kind"] == "gene" and it["group"] == "Disease-causing genes"}
        unassigned = Counter()
        for nid, rels in variants:
            n = self.g.nodes[nid]
            names = {r for r, _, _ in rels}
            cls = ("pathogenic" if names & {"has_pathogenic_variant", "pathogenic_for"} else
                   "likely pathogenic" if names & {"has_likely_pathogenic_variant",
                                                   "likely_pathogenic_for"} else
                   "uncertain" if any("uncertain" in r or "conflicting" in r for r in names)
                   else "benign" if any("benign" in r for r in names) else "reported")
            if cls == "benign":
                continue
            gs = [s.upper() for s in (n.get("info") or {}).get("genes") or ()]
            gs += [m.upper() for m in re.findall(r"\(([A-Za-z0-9-]+)\):", n["label"])]
            target = next((genes[s] for s in gs if s in genes), None)
            if target is None and len(genes) == 1:
                target = next(iter(genes.values()))
            if target is None:
                unassigned[cls] += 1
                continue
            target.setdefault("variants", Counter())[cls] += 1
        for it in genes.values():
            if "variants" in it:
                v = it["variants"]
                it["variants"] = dict(v)
                it["notes"].append("ClinVar / Monarch variants seen: " +
                                   ", ".join(f"{c} {k}" for k, c in v.most_common()))
        if unassigned:
            self.notes.append("variants not tied to a listed gene: " +
                              ", ".join(f"{c} {k}" for k, c in unassigned.most_common()))

    # -- 2. diseases with similar symptoms (HPO annotations) -----------------------------
    def add_similar(self):
        if not self.hpo or not (self.hpo_ids or self.subtype_share) or self.similar_n <= 0:
            return
        h = self.hpo
        mine = {hp: f for hp, f in (self.freq if self.hpo_ids else self.subtype_share).items()
                if not h.is_inheritance(hp)}
        if len(mine) < 3:
            return
        cands = h.rank_diseases(list(mine), top=60)
        own = {normalize(i) for i in self.focus_ids}
        prop_mine = _propagate(h, mine)
        self_score = _sim_dir(h, mine, prop_mine)
        known: dict[str, str] = {}  # OMIM/ORPHA id -> graph node already in the view
        for nid in list(self.items):
            for x in (self.g.ids(nid) if nid in self.g.nodes else {nid}):
                known[normalize(x)] = nid
            for x in self.subtypes.get(nid, ()):
                known.setdefault(x, nid)
        scored = []
        for r in cands:
            ids = {r["id"], *r["xrefs"]}
            if ids & own:
                continue
            theirs = {}
            for d in ids:
                for hp, f in h.ann.get(d, {}).items():
                    if not h.is_inheritance(hp):
                        theirs[hp] = max(theirs.get(hp, 0), f)
            if len(theirs) < 3:
                continue
            prop_theirs = _propagate(h, theirs)
            a = _sim_dir(h, mine, prop_theirs) / self_score
            b = _sim_dir(h, theirs, prop_mine) / _sim_dir(h, theirs, prop_theirs)
            scored.append((math.sqrt(a * b), r, ids, theirs, prop_theirs))
        scored.sort(key=lambda x: -x[0])
        added = 0
        for sim, r, ids, theirs, prop_theirs in scored:
            if added >= self.similar_n or sim < 0.25:
                break
            hit = next((known[i] for i in ids if i in known), None)
            shared = [hp for hp in self.items if hp in prop_theirs
                      and self.items[hp]["section"] == "symptoms"]
            if hit:  # already shown (subtype, related): just note the similarity
                self.items[hit]["notes"].append(f"symptom similarity {sim:.0%}")
                self.items[hit]["similarity"] = round(sim, 3)
                nid = hit
            else:
                nid = sorted(ids)[0]
                self.items[nid] = {
                    "id": nid, "label": r["name"], "kind": "disease", "section": "related",
                    "group": "Diseases with similar symptoms",
                    "notes": [f"symptom similarity {sim:.0%} (HPO annotations), "
                              f"{len(shared)} shared symptoms listed here"],
                    "sources": ["HPO"], "score": sim, "similarity": round(sim, 3),
                    "links": self._links(nid, {"xrefs": sorted(ids - {nid}), "info": {}})}
                added += 1
            for hp in shared:
                self._link(nid, hp, "also has this symptom", "hpo")
        # related diseases already in the view: which symptoms they share (hpo annotation)
        for nid, it in self.items.items():
            if it["section"] != "related" or it["group"] == "Diseases with similar symptoms":
                continue
            ids = [normalize(x) for x in self.g.ids(nid) if normalize(x) in h.ann]\
                if nid in self.g.nodes else []
            ids += [x for x in self.subtypes.get(nid, ()) if x not in ids]
            theirs = {}
            for d in ids:
                theirs.update(h.ann[d])
            if not theirs:
                continue
            it["annotated"] = True  # a concrete disease, not a category
            prop = _propagate(h, theirs)
            for hp, s in list(self.items.items()):
                if s["section"] == "symptoms" and hp in prop:
                    self._link(nid, hp, "also has this symptom", "hpo")
                    it["score"] += 0.02  # concrete, comparable diseases before categories

    # -- 3. group items --------------------------------------------------------------------
    def group(self):
        self._prune()
        self._group_symptoms()
        for section in ("support", "care"):
            items = [it for it in self.items.values() if it["section"] == section and not it["group"]]
            for it in items:
                it["group"] = it.get("country") or "International / not stated"
            self._merge_small(items, "Other countries", min_size=2,
                              keep_first=lambda g: g == "International / not stated")
        for it in self.items.values():
            if it["section"] == "care" and it["kind"] == "expert_centre":
                it["group"] = f"Expert centres: {it['group']}"
            if it["section"] == "support":
                it["group"] = f"Patient organisations: {it['group']}"
        self._dedupe()

    def _prune(self):
        """Drop what is too unspecific to help: entries listed only for a broad disease
        group whose name shares nothing with this disease or its groups (national registries,
        general sequencing projects), and all but the most specific broader groups."""
        for it in self.items.values():
            # PrimeKG / Monarch neighbours without symptom annotations are categories
            # ("metabolic disease", "genetic skin disease"), not relatives
            if it["group"] == "Closely related conditions" and not it.get("annotated"):
                it["group"] = "Broader disease groups"
                it["score"] += 0.1 * len(words(it["label"]))
        broader = [it for it in self.items.values() if it["group"] == "Broader disease groups"]
        broader.sort(key=lambda it: -it["score"])
        for it in broader[MAX_BROADER:]:
            del self.items[it["id"]]
        vocab = set(self.vocab)
        for it in broader[:MAX_BROADER]:
            vocab |= words(it["label"])
        vocab -= {"rare", "genetic", "hereditary", "inherited", "congenital", "human"}
        for nid, it in list(self.items.items()):
            if it["section"] in ("research", "care") and it["kind"] != "expert_centre" and\
                    any(n.startswith("listed for a broader group") for n in it["notes"]) and\
                    not words(it["label"]) & vocab:
                del self.items[nid]

    def _dedupe(self):
        """Entries of one group with the same name become one item ("8 sites"): the
        Orphanet / EURORDIS copies of an organisation, the sites of one reference centre."""
        seen: dict[tuple[str, str], str] = {}
        merged: dict[str, str] = {}
        for nid, it in list(self.items.items()):
            key = (it["group"], _norm_label(it["label"]))
            if key not in seen:
                seen[key] = nid
                continue
            keep = self.items[seen[key]]
            keep["count"] = keep.get("count", 1) + 1
            keep["sources"] = sorted(set(keep["sources"]) | set(it["sources"]))
            urls = {link_item["url"] for link_item in keep["links"]}
            keep["links"] += [link_item for link_item in it["links"] if link_item["url"] not in urls]
            keep["notes"] += [n for n in it["notes"] if n not in keep["notes"]]
            keep["score"] = max(keep["score"], it["score"])
            merged[nid] = seen[key]
            del self.items[nid]
        for it in self.items.values():
            if it.get("count", 1) > 1:
                if it["kind"] == "expert_centre":  # one centre name, several hospitals
                    it["notes"].insert(0, f"{it['count']} sites")
                it["links"] = it["links"][:12]
            it.pop("count", None)
            it.pop("annotated", None)
        if merged:
            old, self.links = self.links, {}
            for link in old.values():
                self._link(merged.get(link["a"], link["a"]), merged.get(link["b"], link["b"]),
                           link["label"], link["origin"])

    def _group_symptoms(self):
        items = [it for it in self.items.values() if it["section"] == "symptoms"]
        if not items:
            return
        tops = {}
        if self.hpo:
            for c in self.hpo.children.get(_hpoa.PHENOTYPE_ROOT, ()):
                tops[c] = BODY_SYSTEMS.get(c) or re.sub(
                    r"^(abnormality of (the )?|abnormal )", "", self.hpo.name[c], flags=re.I
                ).capitalize()
        cats: dict[str, list[str]] = {}
        for it in items:
            anc = self.hpo.ancestors(it["id"]) if self.hpo and it["id"] in self.hpo.name else ()
            cats[it["id"]] = [tops[a] for a in anc if a in tops]
            if it["id"] in tops or it["id"] == _hpoa.PHENOTYPE_ROOT:
                it["score"] -= 2  # the branch itself ("Abnormality of the eye") says little
        # a symptom under several systems goes to the one most other symptoms share
        weight = Counter(c for cs in cats.values() for c in set(cs))
        for it in items:
            cs = cats[it["id"]]
            it["group"] = max(cs, key=lambda c: (weight[c], c)) if cs else "Other features"
        self._merge_small(items, "Other features", min_size=2)

    def _merge_small(self, items, other: str, min_size: int = 1, keep_first=None):
        """At most MAX_GROUPS groups per section: the smallest merge into `other`."""
        if not items:
            return
        most = MAX_GROUPS.get(items[0]["section"], DEFAULT_MAX_GROUPS)
        sizes = Counter(it["group"] for it in items)
        ranked = [g for g, c in sorted(sizes.items(), key=lambda x: (-x[1], x[0]))
                  if g != other and (c >= min_size or (keep_first and keep_first(g)))]
        keep = set(ranked[:most - 1] if len(sizes) > most else ranked)
        for it in items:
            if it["group"] not in keep:
                if it["group"] != other and it["section"] in ("support", "care"):
                    it["notes"].append(it["group"])  # keep the country visible
                it["group"] = other

    # -- 4. connections between kept items -------------------------------------------------
    def _link(self, a: str, b: str, label: str, origin: str):
        if a == b or a not in self.items or b not in self.items:
            return
        if self.items[a]["group"] == self.items[b]["group"] and origin != "graph":
            return  # within one group: adds clutter, not information
        key = (min(a, b), max(a, b), label)
        self.links.setdefault(key, {"a": a, "b": b, "label": label, "origin": origin})

    def connect(self):
        kept = set(self.items)
        for e in self.g.data["edges"]:  # 1. what main.py found between kept items
            u, v, rel = e["from"], e["to"], e["relation"]
            if u in kept and v in kept and rel not in ALIAS_RELATIONS:
                self._link(u, v, humanize(rel), "graph")
        self._connect_primekg()
        self._connect_text()

    def _connect_primekg(self):
        if not PRIMEKG_DB.exists():
            self.notes.append("PrimeKG index missing: no PrimeKG connections")
            return
        db = sqlite3.connect(f"file:{PRIMEKG_DB}?mode=ro", uri=True)
        try:
            idx: dict[int, str] = {}
            for nid, it in self.items.items():
                ids = self.g.ids(nid) if nid in self.g.nodes else {nid}
                row = None
                for c in sorted(ids):
                    if _prefix(c) in ("MONDO", "HP", "DrugBank", "NCBIGene", "UBERON"):
                        row = db.execute("SELECT idx FROM nodes WHERE curie=?", (c,)).fetchone()
                        if row:
                            break
                if not row and it["kind"] == "gene":
                    row = db.execute("SELECT idx FROM nodes WHERE name_lc=? AND type='gene'",
                                     (it["label"].lower(),)).fetchone()
                if not row and it["kind"] == "drug":
                    row = db.execute("SELECT idx FROM nodes WHERE name_lc=? AND type='drug'",
                                     (it["label"].lower(),)).fetchone()
                if row:
                    idx[row[0]] = nid
            keys = list(idx)
            for i in range(0, len(keys), 400):
                chunk = keys[i:i + 400]
                q = ",".join("?" * len(chunk))
                qa = ",".join("?" * len(keys))
                for x, y, rel in db.execute(
                        f"SELECT x, y, rel FROM edges WHERE x IN ({q}) AND y IN ({qa})",
                        (*chunk, *keys)):
                    lab = PRIMEKG_RELATIONS.get(rel, rel)
                    a, b = idx[x], idx[y]
                    ka, kb = (KIND_ORDER.get(self.items[n]["kind"], 9) for n in (a, b))
                    if not lab or (ka, a) > (kb, b):
                        continue  # stored both ways: keep drug -> gene -> disease -> symptom
                    if ka == kb == KIND_ORDER["phenotype"]:
                        continue  # symptom hierarchy: already grouped by body system
                    self._link(a, b, lab, "primekg")
        except sqlite3.Error as e:
            self.notes.append(f"PrimeKG lookup failed: {e}")
        finally:
            db.close()

    def _connect_text(self):
        """A trial / project / registry naming a kept drug, gene, disease or symptom."""
        targets = []
        for nid, it in self.items.items():
            lab = it["label"].strip()
            if it["kind"] == "gene" and len(lab) >= 3:
                targets.append((nid, re.compile(rf"(?<![A-Za-z0-9-]){re.escape(lab)}(?![A-Za-z0-9-])")))
            elif it["kind"] == "drug" and len(lab) >= 5:
                targets.append((nid, re.compile(rf"\b{re.escape(lab)}\b", re.I)))
            elif it["kind"] in ("disease", "phenotype"):
                # "Loeys-Dietz syndrome 2" is named as "Loeys-Dietz syndromes"; a symptom
                # needs a distinctive name ("Dural ectasia", not "Myopia" inside a word)
                base = re.sub(r"(,|\s+(type\s+)?)[0-9IVX]+[A-Z]?$", "", lab).strip()
                if len(base) < 8 or (it["kind"] == "phenotype" and len(words(base)) < 2
                                     and len(base) < 10):
                    continue
                if it["kind"] == "disease" and words(base) <= self.vocab:
                    continue  # "Marfan syndrome type 2" -> "Marfan syndrome": the focus itself
                if self.hpo and it["kind"] == "phenotype" and nid in self.hpo.name and\
                        self.hpo.specificity(nid) < 0.3:
                    continue  # "Abnormality of the eye" names nothing specific
                targets.append((nid, re.compile(rf"\b{re.escape(base)}(e?s)?\b", re.I)))
        for nid, it in self.items.items():
            if it["kind"] not in ("clinical_trial", "research_project", "registry",
                                  "expert_network", "biobank"):
                continue
            text = it["label"] + " " + (it.get("description") or "")
            for tid, rx in targets:
                if rx.search(text):
                    self._link(nid, tid, "mentions", "text")

    # -- 5. output ---------------------------------------------------------------------------
    def build(self) -> dict:
        self.collect()
        self.add_similar()
        self.group()
        self.connect()
        f = self.g.nodes[self.focus]
        info = f.get("info") or {}
        groups: dict[str, dict] = {}
        for it in sorted(self.items.values(), key=lambda i: -i["score"]):
            gid = f"group:{it['section']}:{_slug(it['group'])}"
            it["group_id"] = gid
            g = groups.setdefault(gid, {"id": gid, "label": it["group"], "section": it["section"],
                                        "items": []})
            g["items"].append(it["id"])
        for g in groups.values():
            g["count"] = len(g["items"])
        ordered = sorted(groups.values(), key=lambda g: (
            SECTION_ORDER[g["section"]], bool(re.search(r"\bOther\b|not stated", g["label"])),
            -g["count"], g["label"]))
        self.links = {k: link_item for k, link_item in self.links.items()
                      if link_item["a"] in self.items and link_item["b"] in self.items}
        facts = []
        if self.onset:
            facts.append(("Onset", ", ".join(dict.fromkeys(self.onset))))
        inh = [self.items[i]["label"] for g in ordered if g["label"] == "Inheritance"
               for i in g["items"]]
        if inh:
            facts.append(("Inheritance", ", ".join(inh)))
        genes = [self.items[i]["label"] for g in ordered if g["label"] == "Disease-causing genes"
                 for i in g["items"]]
        if genes:
            facts.append(("Gene(s)", ", ".join(genes[:6])))
        syn = [s for s in info.get("synonyms") or () if _norm_label(s) != _norm_label(f["label"])]
        return {
            "focus": {"id": self.focus, "label": f["label"], "description": _description(info),
                      "synonyms": syn[:8], "links": self._links(self.focus, f),
                      "facts": [{"label": a, "value": b} for a, b in facts]},
            "sections": [{"id": s, "label": lab, "color": c} for s, lab, c in SECTIONS
                         if any(g["section"] == s for g in ordered)],
            "groups": ordered,
            "items": {k: {kk: vv for kk, vv in v.items() if kk != "score"}
                      | {"score": round(v["score"], 3)} for k, v in self.items.items()},
            "links": list(self.links.values()),
            "notes": self.notes,
            "source_graph": self.g.data.get("args", {}),
        }


def _description(info: dict) -> str | None:
    descs = info.get("descriptions") or {}
    for s in DESCRIPTION_PREFERENCE + sorted(descs):
        d = descs.get(s)
        if isinstance(d, str) and len(d.strip()) > 20:
            return re.sub(r"\s+", " ", d).strip()
    return None


def _propagate(h, ann: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t, f in ann.items():
        if t not in h.name:
            continue
        for a in h.ancestors(t):
            out[a] = max(out.get(a, 0.0), f)
    return out


def _sim_dir(h, ann: dict[str, float], prop_other: dict[str, float]) -> float:
    """How well the other disease's (propagated) symptoms explain these: per term the IC
    of the most specific shared ancestor, weighted by the term's frequency."""
    total = 0.0
    for t, f in ann.items():
        if t not in h.name:
            continue
        best = max((h.ic(a) for a in h.ancestors(t) if a in prop_other), default=0.0)
        total += best * (0.4 + 0.6 * f)
    return total or 1e-9


# -- writers -------------------------------------------------------------------------------
def write_html(view: dict, path: Path):
    template = (Path(__file__).parent / "present.html").read_text(encoding="utf-8")
    payload = json.dumps(view, ensure_ascii=False).replace("</", "<\\/")
    html = re.sub(r"/\*__DATA__\*/.*?/\*__END__\*/", lambda _: payload, template, flags=re.S)
    path.write_text(html, encoding="utf-8")


def write_md(view: dict, path: Path):
    f, items = view["focus"], view["items"]
    out = [f"# {f['label']}", ""]
    if f["description"]:
        out += [f["description"], ""]
    for fact in f["facts"]:
        out.append(f"- **{fact['label']}:** {fact['value']}")
    if f["synonyms"]:
        out.append(f"- **Also known as:** {'; '.join(f['synonyms'])}")
    if f["links"]:
        out.append("- **Read more:** " + " · ".join(f"[{link_item['label']}]({link_item['url']})" for link_item in f["links"]))
    by_item = defaultdict(list)
    for link_item in view["links"]:
        by_item[link_item["a"]].append((link_item["label"], link_item["b"]))
        by_item[link_item["b"]].append((link_item["label"], link_item["a"]))
    for sec in view["sections"]:
        out += ["", f"## {sec['label']}"]
        for g in (g for g in view["groups"] if g["section"] == sec["id"]):
            out += ["", f"### {g['label']} ({g['count']})", ""]
            for iid in g["items"]:
                it = items[iid]
                link = it["links"][0]["url"] if it["links"] else None
                name = f"[{it['label']}]({link})" if link else it["label"]
                bits = [x for x in (it.get("frequency"), it.get("status"),
                                    f"phase {it['phase']}" if it.get("phase") else None,
                                    it.get("description") if it["kind"] == "gene" else None,
                                    *it["notes"]) if x]
                conn = sorted({items[o]["label"] for _, o in by_item.get(iid, ())})
                if conn:
                    bits.append("linked to: " + ", ".join(conn[:6]) + (" …" if len(conn) > 6 else ""))
                out.append(f"- {name}" + (f" — {'; '.join(bits)}" if bits else ""))
    out += ["", "---", f"{len(items)} items in {len(view['groups'])} groups, "
            f"{len(view['links'])} connections between items "
            f"({', '.join(f'{k} {v}' for k, v in Counter(link_item['origin'] for link_item in view['links']).most_common())})."]
    out += [f"Note: {n}" for n in view["notes"]]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# -- candidate overview (symptom / gene input) -------------------------------------------
def _disease_ann(h, ids) -> dict[str, float]:
    """HPO annotations of a disease over all its entries (OMIM and Orphanet)."""
    out: dict[str, float] = {}
    for i in ids:
        for hp, f in h.ann.get(normalize(i), {}).items():
            out[hp] = max(out.get(hp, 0.0), f)
    return out


def distinguishing(h, cands: list[dict], n: int = 5, min_freq: float = 0.3) -> dict[str, list]:
    """Per candidate: frequent, specific symptoms (HPO annotations) that none of the
    other candidates has, not even as a more specific or broader-but-informative term:
    what to look for to tell the candidates apart. Returns {ranking id: [(hp, name,
    freq)]}; the searched symptoms are left out."""
    searched = {m["query"] for r in cands for m in r["matches"]}
    anns = {r["id"]: _disease_ann(h, [r["id"], *r["xrefs"]]) for r in cands}
    props = {k: _propagate(h, a) for k, a in anns.items()}
    out = {}
    for r in cands:
        others = [props[o["id"]] for o in cands if o["id"] != r["id"]]
        rows = []
        for hp, f in anns[r["id"]].items():
            if f < min_freq or hp in searched or hp not in h.name or h.is_inheritance(hp):
                continue
            if _hpoa.PHENOTYPE_ROOT not in h.ancestors(hp) or h.specificity(hp) < 0.35:
                continue
            # another candidate has it (or a more specific form of it): not distinguishing
            if any(hp in o for o in others):
                continue
            rows.append((h.specificity(hp) * (0.5 + f), hp, h.name[hp], f))
        rows.sort(reverse=True)
        out[r["id"]] = [(hp, name, f) for _, hp, name, f in rows[:n]]
    return out


def write_overview(g: Graph, query: dict, profiles: dict[str, Path], path: Path,
                   top_n: int):
    """Markdown overview of a symptom / gene search: how the input was read, the ranked
    candidates with the reasons, what tells the top ones apart, links to the profiles."""
    h = _hpoa.load()
    out = [f"# Candidate diseases for: {query['text']}", ""]
    out += ["## How the input was read", "", "| input | read as | term | match |",
            "|---|---|---|---|"]
    for p in query["parts"]:
        if p["kind"] == "unknown":
            sugg = "; ".join(f"{name} `{hp}`" for hp, name in p.get("suggestions") or ())
            out.append(f"| {p['text']} | **not recognised** (not used) | closest HPO terms: "
                       f"{sugg or '-'} | |")
        else:
            alt = f" — {p['alternative']}" if p.get("alternative") else ""
            out.append(f"| {p['text']} | {p['kind']} | {p.get('label')} `{p.get('id')}` | "
                       f"{p.get('how')}{alt} |")
    phen = [p for p in query["parts"] if p["kind"] == "phenotype"]
    genes = [p for p in query["parts"] if p["kind"] == "gene"]
    out += ["", "## Ranked candidates", "",
            "Ranked by: " + ", ".join(x for x in (
                "diseases named in the input first" if any(r["pinned"] for r in query["ranking"])
                else "",
                "diseases caused by the searched gene(s) first, then other gene links" if genes
                else "",
                "how well the disease's HPO annotations cover the symptoms (specific "
                "symptoms count more; ✓ has the symptom, ~ only a related broader one)" if phen
                else "",
                "better characterised diseases (more HPO annotations) on ties") if x) + ".",
            "", "| # | disease | why | profile |", "|---|---|---|---|"]
    first: dict[str, int] = {}  # graph node -> rank of its first entry
    heads: list[dict] = []  # first entry of each graph node
    for i, r in enumerate(query["ranking"], 1):
        why = []
        if r["pinned"]:
            why.append("named in the input")
        assoc = {}
        for x in r["genes"]:
            assoc.setdefault(x["symbol"], x["association"])
        why += [f"{sym}: {a.lower()}" for sym, a in assoc.items()]
        if phen:
            full = sum(m["full"] for m in r["matches"])
            ms = [f"{'✓' if m['full'] else '~'} {m['query_label']}"
                  + ("" if m["full"] else f" (via {m['matched_label']})")
                  + f" {m['frequency']:.0%}" for m in r["matches"]]
            why.append(f"{full}/{len(phen)} symptoms (score {r['score']:.1f}): "
                       + "; ".join(ms))
        ids = ", ".join([r["id"], *r["xrefs"]])
        if r["node"] in first:  # merged into the same entity as a better-ranked entry
            cell = f"same entity as #{first[r['node']]}"
        else:
            first[r["node"]] = i
            prof = profiles.get(r["node"])
            cell = f"[profile]({prof.name})" if prof else "-"
            heads.append(r)
        out.append(f"| {i} | {r['name']} `{ids}` | {'<br>'.join(why) or '-'} | {cell} |")
    top = [r for r in heads if r["node"] in profiles] or heads[:top_n]
    if h and len(top) > 1:
        diff = distinguishing(h, top)
        out += ["", "## Telling the top candidates apart", "",
                "Frequent (≥30%), specific symptoms annotated for one candidate and for none "
                "of the others (HPO annotations): worth checking for.", ""]
        for r in top:
            rows = diff.get(r["id"]) or []
            out.append(f"- **{r['name']}**: " + ("; ".join(
                f"{name} ({freq_label(f)})" for _, name, f in rows)
                if rows else "no distinguishing symptom annotated"))
    if profiles:
        out += ["", "## Profiles", ""]
        for r in heads:
            if r["node"] in profiles:
                out.append(f"- [{r['name']}]({profiles[r['node']].name})")
    rest = len(heads) - len(profiles)
    if rest > 0:
        out += ["", f"{rest} candidates without a profile here: main.py queried them only "
                "briefly (raise --focus-candidates there, or present.py --top for a thinner "
                "profile)."]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# -- query-centred view ----------------------------------------------------------------
QUERY_SECTIONS = [  # id, label, colour (viewer)
    ("candidates", "Candidate diseases", "#b07aa1"),
    ("searched", "Searched terms", "#e15759"),
    ("symptoms", "Telling them apart", "#d59683"),
    ("shared", "Shared by many candidates", "#c4a0a6"),
    ("genetics", "Genetics", "#4e79a7"),
]


def query_view(g: Graph, top: int = 12, apart: int = 3, shared: int = 10) -> dict:
    """The overview of a symptom / gene search (main.py "HP:0001627, chest pain") in the
    format of Present.build(): the search in the centre, around it the ranked candidate
    diseases (grouped by how many searched terms they match), the searched terms, the
    findings that tell the candidates apart (distinguishing()), findings many of them
    share and their disease-causing genes. A broad search fits many diseases about equally;
    this shows them side by side instead of the profile of whichever ranked first."""
    query = g.data["query"]
    h = _hpoa.load()
    parts = [p for p in query["parts"] if p.get("kind") in ("phenotype", "gene", "disease")]
    by_node: dict[str, dict] = {}  # entries merged into one entity: the best-ranked one
    for r in query.get("ranking") or ():
        by_node.setdefault(r["node"], r)
    ranked = list(by_node.values())[:top]
    n_terms = len({m["query"] for r in ranked for m in r["matches"]}) or len(parts)
    items: dict[str, dict] = {}
    links: list[dict] = []

    def item(iid, label, kind, section, group, score, **extra):
        items[iid] = {"id": iid, "label": label, "kind": kind, "section": section,
                      "group": group, "notes": [], "sources": ["HPO"], "links": [],
                      "score": round(score, 3), **extra}

    def link(a, b, label):
        links.append({"a": a, "b": b, "label": label, "origin": "hpo"})

    for p in parts:
        pid = p.get("id") or p["text"]
        kind = {"phenotype": "phenotype", "gene": "gene"}.get(p["kind"], "disease")
        item(pid, p.get("label") or p["text"], kind, "searched",
             {"phenotype": "Symptoms & findings", "gene": "Genes"}.get(p["kind"], "Diseases"),
             1.0, description=f"You searched for “{p['text']}”.",
             links=[{"label": URL_PATTERNS["HP"][0], "url": URL_PATTERNS["HP"][1].format(_local(pid))}]
             if _prefix(pid) == "HP" else [])

    for rank, r in enumerate(ranked, 1):
        node = g.nodes.get(r["node"], {"label": r["name"], "kind": "disease", "xrefs": r["xrefs"]})
        full = [m for m in r["matches"] if m.get("full")]
        matched = len({m["query"] for m in r["matches"]})
        group = (f"Match all {n_terms} terms" if matched >= n_terms and n_terms > 1
                 else f"Match {matched} of {n_terms} terms" if n_terms > 1 else "Matches")
        found = "; ".join(
            f"{m['query_label']}" + ("" if m.get("full") else f" (as {m['matched_label']})")
            + (f", {freq_label(m['frequency'])}" if freq_label(m.get("frequency")) else "")
            for m in r["matches"])
        desc = _description(node.get("info") or {})
        item(r["node"], r["name"], "disease", "candidates", group, r["score"],
             description=f"Rank {rank}, score {r['score']:.1f}. Matches: {found or 'none'}."
             + (f"\n{desc}" if desc else ""),
             links=node_links(r["node"], node))
        for m in r["matches"]:
            if m["query"] in items:
                link(m["query"], r["node"], "matches" if m.get("full") else "partly matches")

    if h and ranked:
        anns = {r["node"]: _disease_ann(h, [r["id"], *r["xrefs"]]) for r in ranked}
        searched = {m["query"] for r in ranked for m in r["matches"]}
        searched_anc = set().union(*(h.ancestors(s) for s in searched if s in h.name))
        # findings that tell the candidates apart
        apart_of = distinguishing(h, ranked, n=apart)
        for r in ranked:
            for hp, name, f in apart_of[r["id"]]:
                if hp not in items:
                    item(hp, name, "phenotype", "symptoms", "", h.specificity(hp),
                         description="Recorded for only one of the candidates.",
                         links=[{"label": "HPO", "url": URL_PATTERNS["HP"][1].format(_local(hp))}])
                link(r["node"], hp, f"has ({freq_label(f)})" if freq_label(f) else "has")
        # findings most candidates share (besides the searched ones)
        props = {k: _propagate(h, a) for k, a in anns.items()}
        counts = Counter(t for a in anns.values() for t in a
                         if t in h.name and t not in searched_anc and not h.is_inheritance(t)
                         and _hpoa.PHENOTYPE_ROOT in h.ancestors(t) and h.specificity(t) >= 0.3)
        common = sorted((t for t, c in counts.items() if c >= max(2, len(ranked) // 3)),
                        key=lambda t: (-counts[t], -h.specificity(t)))[:shared]
        for t in common:
            if t in items:
                continue
            item(t, h.name[t], "phenotype", "shared", "", counts[t] + h.specificity(t),
                 description=f"Recorded for {counts[t]} of the {len(ranked)} candidates.",
                 links=[{"label": "HPO", "url": URL_PATTERNS["HP"][1].format(_local(t))}])
            for r in ranked:
                if t in props[r["node"]]:
                    link(r["node"], t, "has")
        # body systems as groups, as in the disease view
        tops = {c: BODY_SYSTEMS.get(c) or re.sub(r"^(abnormality of (the )?|abnormal )", "",
                                                 h.name[c], flags=re.I).capitalize()
                for c in h.children.get(_hpoa.PHENOTYPE_ROOT, ())}
        for it in items.values():
            if it["section"] in ("symptoms", "shared"):
                sys_ = [tops[a] for a in h.ancestors(it["id"]) if a in tops]
                it["group"] = sys_[0] if sys_ else "Other features"
        # disease-causing genes of the candidates (HPO genes_to_disease)
        genes: dict[str, list[str]] = defaultdict(list)
        for sym, links_ in h.g2d.items():
            for dis, assoc in links_:
                if _hpoa.causal(assoc):
                    genes[dis].append(sym)
        for r in ranked:
            for sym in dict.fromkeys(s for i in [r["id"], *r["xrefs"]] for s in genes.get(normalize(i), ())):
                gid = sym if sym in items else f"gene:{sym}"
                if gid not in items:
                    item(gid, sym, "gene", "genetics", "Disease-causing genes", 1.0,
                         links=[{"label": "NCBI Gene", "url": "https://www.ncbi.nlm.nih.gov/gene/?term="
                                 f"{sym}%5Bsym%5D+AND+human%5Borgn%5D"}])
                link(r["node"], gid, "caused by")

    groups: dict[str, dict] = {}
    for it in sorted(items.values(), key=lambda i: -i["score"]):
        gid = f"group:{it['section']}:{_slug(it['group'] or it['section'])}"
        it["group_id"] = gid
        groups.setdefault(gid, {"id": gid, "label": it["group"] or dict(
            (s, lab) for s, lab, _ in QUERY_SECTIONS)[it["section"]], "section": it["section"],
            "items": []})["items"].append(it["id"])
    order = {s: i for i, (s, _, _) in enumerate(QUERY_SECTIONS)}
    for gr in groups.values():
        gr["count"] = len(gr["items"])
    ordered = sorted(groups.values(), key=lambda gr: (
        order[gr["section"]], gr["label"].startswith("Other"), -gr["count"], gr["label"]))
    seen, unique_links = set(), []
    for lk in links:
        key = (lk["a"], lk["b"])
        if key not in seen and lk["a"] in items and lk["b"] in items:
            seen.add(key)
            unique_links.append(lk)
    terms = [p.get("label") or p["text"] for p in parts]
    many = len(by_node) > top
    facts = [{"label": "Searched", "value": ", ".join(terms)},
             {"label": "Candidates", "value": f"{len(ranked)}" + (f" of {len(by_node)} shown" if many else "")}]
    if ranked:
        facts.append({"label": "Best match", "value": ranked[0]["name"]})
    return {
        "focus": {"id": g.data.get("start") or "query", "label": " · ".join(terms) or query["text"],
                  "description": (
                      f"{len(ranked)} diseases match these terms, ranked by how well their HPO "
                      "annotations cover them (specific, frequent findings count most). Close "
                      "scores mean the search fits them about equally: add a more specific "
                      "finding or a gene to narrow it down."),
                  "synonyms": [], "links": [], "facts": facts},
        "sections": [{"id": s, "label": lab, "color": c} for s, lab, c in QUERY_SECTIONS
                     if any(gr["section"] == s for gr in ordered)],
        "groups": ordered,
        "items": items,
        "links": unique_links,
        "notes": [],
        "source_graph": g.data.get("args", {}),
        "query": {"text": query["text"], "mode": query.get("mode")},
    }


def _write(view: dict, out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix == ".md":
        write_md(view, out)
    elif out.suffix == ".json":
        out.write_text(json.dumps(view, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        write_html(view, out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("graph", type=Path, help="graph JSON written by main.py (-o run.json)")
    ap.add_argument("-o", "--out", type=Path,
                    help="output .html (default: <graph>.present.html), .md or .json")
    ap.add_argument("--focus", help="disease to present (id or exact label); default: the "
                                    "focus main.py found (symptom mode: the top candidate)")
    ap.add_argument("--similar", type=int, default=8,
                    help="max diseases with similar symptoms to add (0: none)")
    ap.add_argument("--subtype-symptoms", action=argparse.BooleanOptionalAction, default=True,
                    help="a disease group without HPO annotations of its own (MONDO's "
                         "'juvenile neuronal ceroid lipofuscinosis' for 'Batten disease') "
                         "shows the symptoms its subtypes share (on by default; "
                         "--no-subtype-symptoms switches it off)")
    ap.add_argument("--top", type=int,
                    help="symptom / gene search: profiles for this many top candidates "
                         "(default: the ones main.py expanded fully)")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    g = Graph(json.loads(args.graph.read_text(encoding="utf-8")))
    out = args.out or args.graph.with_suffix(".present.html")
    query = g.data.get("query")
    if query and query.get("ranking") and not args.focus:
        # symptom / gene search: an overview of the candidates + a profile for the top ones
        by_node: dict[str, dict] = {}  # entries merged into one entity: the best-ranked one
        for r in query["ranking"]:
            if r["node"] in g.nodes:
                by_node.setdefault(r["node"], r)
        ranked = list(by_node.values())
        n = args.top or len(set(g.data.get("focus") or ())) or 3
        profiles: dict[str, Path] = {}
        for i, r in enumerate(ranked[:n], 1):
            view = Present(g, r["node"], similar=args.similar,
                           subtype_symptoms=args.subtype_symptoms).build()
            path = out.with_name(f"{out.stem}.{i}-{_slug(r['name'])}{out.suffix}")
            _write(view, path)
            profiles[r["node"]] = path
            print(f"{i}. {view['focus']['label']} ({r['node']}): {len(view['items'])} items in "
                  f"{len(view['groups'])} groups -> {path.resolve()}")
        overview = out if out.suffix == ".md" else out.with_suffix(".md")
        query = {**query, "ranking": [r for r in query["ranking"] if r["node"] in g.nodes]}
        write_overview(g, query, profiles, overview, n)
        print(f"candidate overview -> {overview.resolve()}")
        return
    focus = g.pick_focus(args.focus)
    if not focus:
        ap.error(f"focus {args.focus!r} not found in the graph" if args.focus
                 else "the graph has no disease to present")
    view = Present(g, focus, similar=args.similar,
                   subtype_symptoms=args.subtype_symptoms).build()
    _write(view, out)
    print(f"{view['focus']['label']} ({focus}): {len(view['items'])} items in "
          f"{len(view['groups'])} groups (from {len(g.nodes)} nodes), "
          f"{len(view['links'])} connections -> {out.resolve()}")
    for n in view["notes"]:
        print("note:", n)
    cands = [o for o, rel, _, out in g.adj.get(g.data.get("start"), ())
             if out and rel == "candidate_disease" and o != focus]
    if cands and not args.focus:
        print("other candidate diseases of the search (present one with --focus):")
        for o in cands:
            print(f"  {o}  {g.nodes[o]['label']}")


if __name__ == "__main__":
    main()
