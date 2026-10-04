#!/usr/bin/env python3
"""Patient-facing view of a graph from main.py: only what helps someone who has (or cares
for someone with) a specific rare disease learn more about it.

Input is the graph JSON of main.py (-o run.json). The disease the query was about (its
"focus") becomes the centre; its direct neighbours are kept when they are useful to a
patient and sorted into sections, and within a section into groups that become one node
each (the items are listed in the node and can be expanded in the viewer):

  Symptoms        grouped by body system (top-level HPO branches), most frequent first;
                  frequencies from the HPO annotations
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

Usage:
  python src/query-test/present.py runs/marfan.json                 # -> runs/marfan.present.html
  python src/query-test/present.py runs/pmm2.json -o runs/pmm2.present.md
  python src/query-test/present.py runs/sym.json --focus MONDO:0009352 -o sym.present.json
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
from sources.base import words  # noqa: E402

PRIMEKG_DB = Path(__file__).resolve().parents[2] / "data" / "primekg" / "primekg.sqlite"
CURIE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*:\S+$")

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
def _prefix(curie: str) -> str:
    return curie.split(":", 1)[0]


def _local(curie: str) -> str:
    return curie.split(":", 1)[1] if ":" in curie else curie


def _norm_label(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "x"


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
                return f
        # fallback: the disease the most sources matched to the input
        start = self.data.get("start")
        votes = Counter(o for o, rel, _, out in self.adj.get(start, ())
                        if out and self.nodes[o]["kind"] == "disease")
        if votes:
            return votes.most_common(1)[0][0]
        diseases = [n for n in self.nodes if self.nodes[n]["kind"] == "disease"]
        return max(diseases, key=lambda n: len(self.adj[n]), default=None)


# -- curated view ----------------------------------------------------------------------
class Present:
    def __init__(self, g: Graph, focus: str, similar: int = 8):
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

    # which nodes are the focus disease under another id (unmerged xrefs / name matches)
    def _aliases(self) -> set[str]:
        out = {self.focus}
        name = _norm_label(self.g.nodes[self.focus]["label"])
        for o, rel, _, _ in self.g.adj[self.focus]:
            n = self.g.nodes[o]
            if rel in ALIAS_RELATIONS and n["kind"] == "disease" and _norm_label(n["label"]) == name:
                out.add(o)
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
        searched = set()
        if symptom_query:
            searched = {o for o, rel, _, out in self.g.adj[self.g.data["start"]]
                        if out and rel == "has_symptom"}
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
                    self.items[nid]["notes"].insert(0, "one of the searched symptoms")
                    self.items[nid]["score"] += 1
        self._attach_variants(variants)

    def _classify(self, nid: str, n: dict, rels) -> tuple[str, str, dict] | None:
        kind, names = n["kind"], {r for r, _, _ in rels}
        srcs = {s for _, s, _ in rels}
        if kind in DROP_KINDS or (CURIE.match(n["label"]) and kind != "variant"):
            return None
        if n["label"].strip().title() in COUNTRY_SET or n["label"].strip() in COUNTRY_SET:
            return None  # e.g. an Orphanet trial-network entry named "BELGIUM"
        if kind == "phenotype":
            if names <= {"lacks_phenotype", "phenotype_absent_in", "disease_has_basis_in",
                         "has_material_basis_in", "side_effect"}:
                return None
            if names & {"has_inheritance"} or self._is_inheritance(nid, n):
                return "genetics", "Inheritance", {}
            if self.hpo and nid in self.hpo.name and \
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
        if not self.hpo or not self.hpo_ids or self.similar_n <= 0:
            return
        h = self.hpo
        mine = {hp: f for hp, f in self.freq.items() if not h.is_inheritance(hp)}
        if len(mine) < 3:
            return
        cands = h.rank_diseases(list(mine), top=60)
        own = {normalize(i) for i in self.focus_ids}
        prop_mine = _propagate(h, mine)
        self_score = _sim_dir(h, mine, prop_mine)
        known: dict[str, str] = {}  # OMIM/ORPHA id -> graph node already in the view
        for nid in list(self.items):
            for x in self.g.ids(nid):
                known[normalize(x)] = nid
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
                nid = hit
            else:
                nid = sorted(ids)[0]
                self.items[nid] = {
                    "id": nid, "label": r["name"], "kind": "disease", "section": "related",
                    "group": "Diseases with similar symptoms",
                    "notes": [f"symptom similarity {sim:.0%} (HPO annotations), "
                              f"{len(shared)} shared symptoms listed here"],
                    "sources": ["HPO"], "score": sim,
                    "links": self._links(nid, {"xrefs": sorted(ids - {nid}), "info": {}})}
                added += 1
            for hp in shared:
                self._link(nid, hp, "also has this symptom", "hpo")
        # related diseases already in the view: which symptoms they share (hpo annotation)
        for nid, it in self.items.items():
            if it["section"] != "related" or it["group"] == "Diseases with similar symptoms":
                continue
            ids = [normalize(x) for x in self.g.ids(nid) if normalize(x) in h.ann] \
                if nid in self.g.nodes else []
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
            if it["section"] in ("research", "care") and it["kind"] != "expert_centre" and \
                    any(n.startswith("listed for a broader group") for n in it["notes"]) and \
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
            urls = {l["url"] for l in keep["links"]}
            keep["links"] += [l for l in it["links"] if l["url"] not in urls]
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
                if self.hpo and it["kind"] == "phenotype" and nid in self.hpo.name and \
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
        self.links = {k: l for k, l in self.links.items()
                      if l["a"] in self.items and l["b"] in self.items}
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
        out.append("- **Read more:** " + " · ".join(f"[{l['label']}]({l['url']})" for l in f["links"]))
    by_item = defaultdict(list)
    for l in view["links"]:
        by_item[l["a"]].append((l["label"], l["b"]))
        by_item[l["b"]].append((l["label"], l["a"]))
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
            f"({', '.join(f'{k} {v}' for k, v in Counter(l['origin'] for l in view['links']).most_common())})."]
    out += [f"Note: {n}" for n in view["notes"]]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


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
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    g = Graph(json.loads(args.graph.read_text(encoding="utf-8")))
    focus = g.pick_focus(args.focus)
    if not focus:
        ap.error(f"focus {args.focus!r} not found in the graph" if args.focus
                 else "the graph has no disease to present")
    view = Present(g, focus, similar=args.similar).build()
    out = args.out or args.graph.with_suffix(".present.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix == ".md":
        write_md(view, out)
    elif out.suffix == ".json":
        out.write_text(json.dumps(view, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        write_html(view, out)
    print(f"{view['focus']['label']} ({focus}): {len(view['items'])} items in "
          f"{len(view['groups'])} groups (from {len(g.nodes)} nodes), "
          f"{len(view['links'])} connections -> {out.resolve()}")
    for n in view["notes"]:
        print("note:", n)
    cands = [o for o, rel, _, out in g.adj.get(g.data.get("start"), ())
             if out and rel == "candidate_disease" and o != focus]
    if cands and not args.focus:
        print("other candidate diseases of the symptom search (present one with --focus):")
        for o in cands:
            print(f"  {o}  {g.nodes[o]['label']}")


if __name__ == "__main__":
    main()
