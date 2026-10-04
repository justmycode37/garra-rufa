"""Offline tests of the FINDINGS.md improvements (src/query-test/improvements.py, R1-R9).

No network, no LLM: graphs are built from small synthetic paper records, services are
replaced by fakes."""
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HAS_REQUESTS = importlib.util.find_spec("requests") is not None
if HAS_REQUESTS:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "query-test"))
    try:
        import improvements
        import main
        import resolve
        from entities import Entities
        from evidence import extract, gaps, screen, transfer
        from report import Stats
        from sources import Edge, Node
        from evidence.build import (Graph, canon_key, is_generic, is_non_solution,
                                    qualified_match)
        from evidence.context import Profile, build_profile, sanitize_terms, typed_symptoms
        from evidence.registries import Registries, registry_records
        from literature import plan as lit_plan
    finally:
        sys.path.pop(0)


def profile(**kw) -> "Profile":
    d = {"id": "MONDO:1", "label": "Pompe disease",
         "names": ["Pompe disease", "glycogen storage disease type II"],
         "xrefs": ["MESH:D006009", "ORPHA:365"], "description": ""}
    d.update(kw.pop("disease", {}))
    return Profile(d, **kw)


def rec(key, ents, edges, level="animal", origin="core", text="fulltext"):
    """A paper record as main.read_paper writes it. ents: k -> (id, label, type);
    edges: (subject, predicate, object[, context_disease key[, level[, effect]]])."""
    entities = {k: {"name": lab, "type": typ,
                    "norm": {"id": nid, "label": lab, "xrefs": [],
                             "how": "text" if nid.startswith("text:") else "ontology"}}
                for k, (nid, lab, typ) in ents.items()}
    out = []
    for e in edges:
        s, p, o, cd, lv, eff = list(e) + ["", None, "positive"][len(e) - 3:]
        out.append({"subject": s, "predicate": p, "object": o, "evidence_level": lv or level,
                    "effect": eff, "organism": "", "context_disease": cd or "",
                    "context_model": "", "evidence": [{"passage": "p1", "section": "results",
                                                       "quote": f"{s} {p} {o}"}]})
    return {"key": key, "origin": origin, "text_source": text,
            "meta": {"pmid": key, "title": key, "year": 2020}, "entities": entities,
            "edges": out}


def build(prof, records, spec):
    improvements.configure(spec)
    g = Graph(prof)
    for r in records:
        g.add_paper(r)
    merged = g.merge_duplicates()
    g.score_edges()
    return g, merged


@unittest.skipUnless(HAS_REQUESTS, "Install .[research] to test the research pipeline")
class Base(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop(improvements.ENV, None)
        improvements.configure("all")

    def tearDown(self):
        self._env.stop()
        improvements.configure(None)


class FlagTests(Base):
    def test_parse(self):
        self.assertTrue(all(improvements.parse(None).values()))
        self.assertFalse(any(improvements.parse("none").values()))
        st = improvements.parse("-canonicalize,-gap_check")
        self.assertFalse(st["canonicalize"] or st["gap_check"])
        self.assertTrue(st["input_identity"] and st["diverse_selection"])
        st = improvements.parse("none,+input_identity")
        self.assertEqual([k for k, v in st.items() if v], ["input_identity"])
        self.assertTrue(improvements.parse("none,all")["symptom_axis"])
        with self.assertRaises(ValueError):
            improvements.parse("-no_such_flag")

    def test_env_and_args(self):
        os.environ[improvements.ENV] = "none,+query_hygiene"
        improvements.configure(None)
        self.assertTrue(improvements.on("query_hygiene"))
        self.assertFalse(improvements.on("canonicalize"))

        class Args:
            improvements = "-symptom_axis"
        a = Args()
        improvements.apply_args(a)  # the flag wins over the environment
        self.assertFalse(improvements.on("symptom_axis"))
        self.assertTrue(improvements.on("canonicalize"))
        self.assertEqual(a.improvements, "all,-symptom_axis")
        self.assertEqual(os.environ[improvements.ENV], "all,-symptom_axis")
        self.assertEqual(improvements.parse(a.improvements), improvements.active())


class InputIdentityTests(Base):
    def test_qualified_match(self):
        names = ["Pompe disease", "glycogen storage disease type II"]
        for lab in ("late-onset Pompe disease", "CRIM-negative infantile Pompe disease",
                    "Glycogen Storage Disease Type II"):
            self.assertTrue(qualified_match(lab, names), lab)
        for lab in ("Pompe-like myopathy", "glycogen storage disease type III", "disease",
                    "glycogen storage disease I", "Pompe disease mouse model"):
            self.assertFalse(qualified_match(lab, names), lab)
        npc = ["Niemann-Pick disease type C", "Niemann-Pick disease"]
        self.assertTrue(qualified_match("Niemann-Pick disease, type C1", npc))
        self.assertFalse(qualified_match("Niemann-Pick disease type A", npc))
        self.assertEqual(qualified_match("Rett syndrome", ["Atypical Rett syndrome"]), "parent")
        self.assertFalse(qualified_match("Rett syndrome", ["CDKL5 Rett syndrome deficiency"]))
        # one-word and plural (group) names never match
        self.assertFalse(qualified_match("progeria syndrome type 2", ["progeria"]))
        self.assertFalse(qualified_match("processing-deficient progeroid laminopathies",
                                         ["progeroid laminopathies"]))

    def records(self):
        ents = {"d": ("MONDO:1", "Pompe disease", "disease"),
                "m": ("MESH:D006009", "Glycogen Storage Disease Type II", "disease"),
                "l": ("text:disease:late_onset_pompe_disease", "late-onset Pompe disease",
                      "disease"),
                "o": ("text:disease:pompe_like_myopathy", "Pompe-like myopathy", "disease"),
                "x": ("text:drug:x", "drugx", "drug"), "y": ("text:drug:y", "drugy", "drug"),
                "z": ("text:drug:z", "drugz", "drug"), "g": ("HGNC:1", "GAA", "gene")}
        return [rec("P1", ents, [("x", "treats", "m"), ("y", "treats", "o"),
                                 ("z", "rescues", "g", "l"), ("l", "causes", "g"),
                                 ("d", "causes", "g")])]

    def test_input_ids_and_xref_merge(self):
        g, merged = build(profile(), self.records(), "all")
        self.assertNotIn("MESH:D006009", g.nodes)  # merged into the input
        self.assertIn("MESH:D006009", g.nodes["MONDO:1"]["xrefs"])
        self.assertIn(("text:drug:x", "treats", "MONDO:1"), g.edges)
        ins = g.input_ids()
        self.assertIn("text:disease:late_onset_pompe_disease", ins)
        self.assertNotIn("text:disease:pompe_like_myopathy", ins)
        cs = {c["id"]: c for c in g.candidates()}
        self.assertEqual(cs["text:drug:x"]["category"], "direct")
        self.assertEqual(cs["text:drug:z"]["category"], "direct")  # tested in LOPD patients

    def test_off_is_old_behaviour(self):
        g, merged = build(profile(), self.records(), "none")
        self.assertEqual(merged, 0)
        self.assertIn("MESH:D006009", g.nodes)
        self.assertEqual(g.input_ids(), {"MONDO:1"})
        cs = {c["id"]: c for c in g.candidates()}
        self.assertEqual(cs["text:drug:z"]["category"], "transfer")


class CanonicalizeTests(Base):
    def test_canon_key(self):
        self.assertEqual(canon_key("MOMELOTINIB DIHYDROCHLORIDE", "drug"), "momelotinib")
        self.assertEqual(canon_key("pacritinib citrate", "drug"), "pacritinib")
        self.assertEqual(canon_key("2-hydroxypropyl-β-cyclodextrin", "drug"),
                         canon_key("2-HYDROXYPROPYL-BETA-CYCLODEXTRIN", "drug"))
        self.assertEqual(canon_key("sodium", "drug"), "sodium")  # never empties a name
        self.assertEqual(canon_key("serum sodium", "biomarker"), "serum sodium")

    def records(self):
        return [rec("P1", {"a": ("ChEMBL:1", "MOMELOTINIB DIHYDROCHLORIDE", "drug"),
                           "b": ("text:drug:momelotinib", "momelotinib", "drug"),
                           "c": ("text:drug:hpbcd1", "2-hydroxypropyl-β-cyclodextrin", "drug"),
                           "d": ("text:drug:hpbcd2", "2-HYDROXYPROPYL-BETA-CYCLODEXTRIN",
                                 "drug"),
                           "e": ("text:drug:aval", "avalglucosidase alfa", "drug"),
                           "f": ("text:therapy:aval", "Avalglucosidase Alfa", "therapy"),
                           "g": ("HGNC:1", "ACVR1", "gene")},
                    [("a", "targets", "g"), ("b", "targets", "g"), ("c", "targets", "g"),
                     ("d", "targets", "g"), ("e", "targets", "g"), ("f", "targets", "g")])]

    def test_merges_without_llm(self):
        g, merged = build(profile(), self.records(), "all")
        self.assertEqual(merged, 3)
        self.assertEqual(g.nodes["ChEMBL:1"]["label"], "momelotinib")
        self.assertIn("MOMELOTINIB DIHYDROCHLORIDE", g.nodes["ChEMBL:1"]["names"])
        drugs = [n for n in g.nodes.values() if n["kind"] in ("drug", "therapy")]
        self.assertEqual(len(drugs), 3)
        self.assertEqual(g.edges[("ChEMBL:1", "targets", "HGNC:1")]["papers"], 1)
        self.assertEqual(g.canonicalize_llm(None), 0)

    def test_off(self):
        g, merged = build(profile(), self.records(), "none")
        self.assertEqual(merged, 0)
        self.assertEqual(g.nodes["ChEMBL:1"]["label"], "MOMELOTINIB DIHYDROCHLORIDE")

    def test_llm_groups(self):
        ents = {"a": ("text:outcome_measure:a", "heterotopic ossification volume",
                      "outcome_measure"),
                "b": ("text:outcome_measure:b", "volume of new heterotopic ossification",
                      "outcome_measure"),
                "c": ("text:outcome_measure:c", "number of new lesions", "outcome_measure"),
                "d": ("MONDO:1", "Pompe disease", "disease")}
        g, _ = build(profile(), [rec("P1", ents, [("a", "measures_outcome_of", "d")]),
                                 rec("P2", ents, [("b", "measures_outcome_of", "d"),
                                                  ("c", "measures_outcome_of", "d")])], "all")

        class FakeLlm:
            calls = []

            def chat(self, system, user, max_tokens=0):
                self.calls.append(user)
                lines = [x for x in user.splitlines() if x.startswith("[")]
                idx = [i for i, x in enumerate(lines) if "heterotopic" in x]
                return {"groups": [idx, [99, 98]]}
        llm = FakeLlm()
        self.assertEqual(g.canonicalize_llm(llm), 1)
        self.assertEqual(len(llm.calls), 1)
        kept = [n for n in g.nodes.values() if "heterotopic" in n["label"]]
        self.assertEqual(len(kept), 1)
        self.assertEqual(sorted(kept[0]["papers"]), ["P1", "P2"])


def hgps_records():
    """One mouse paper with 8 rescue edges (methionine restriction) vs an approved drug in
    6 clinical papers (lonafarnib)."""
    ents = {"d": ("MONDO:1", "Pompe disease", "disease"),
            "m": ("text:therapy:met", "methionine restriction", "therapy"),
            "l": ("ChEMBL:9", "lonafarnib", "drug")}
    ents.update({f"p{i}": (f"HP:{i}", f"phenotype {i}", "phenotype") for i in range(8)})
    recs = [rec("MOUSE", ents, [("m", "rescues", f"p{i}", "d") for i in range(8)],
                level="animal")]
    recs += [rec(f"C{i}", ents, [("l", "treats", "d", "d")], level="clinical_trial")
             for i in range(6)]
    return recs


class PaperScoringTests(Base):
    def test_single_paper_does_not_saturate(self):
        g, _ = build(profile(), hgps_records(), "all")
        cs = {c["id"]: c for c in g.candidates()}
        met, lon = cs["text:therapy:met"], cs["ChEMBL:9"]
        self.assertEqual(met["n_papers"], 1)
        self.assertEqual(met["best_level"], "animal")
        self.assertEqual(met["tier"], 1)
        self.assertLessEqual(met["score"], 0.5)
        self.assertEqual(lon["n_papers"], 6)
        self.assertEqual(lon["tier"], 3)
        self.assertGreater(lon["score"], met["score"] + 0.4)

    def test_none_reproduces_old_scoring(self):
        g, _ = build(profile(), hgps_records(), "none")
        cs = {c["id"]: c for c in g.candidates()}
        # old: noisy-OR over 8 edges of 0.5 x 1.0 (tested in the input's patients)
        self.assertEqual(cs["text:therapy:met"]["score"], round(1 - 0.5 ** 8, 3))
        self.assertEqual(cs["ChEMBL:9"]["score"], round(1 - 0.1 ** 6, 3))
        self.assertEqual(set(cs["ChEMBL:9"]), {"id", "label", "kind", "score", "category",
                                               "tested_without_benefit_in_input",
                                               "found_by", "papers", "paths"})
        self.assertNotIn("paper_w", next(iter(g.edges.values())))


class GenericTests(Base):
    def test_is_generic(self):
        for lab in ("Sanger sequencing", "whole-exome sequencing", "brain MRI", "western blot",
                    "computed tomography", "histological analysis", "RT-qPCR",
                    "enzyme-linked immunosorbent assay"):
            self.assertTrue(is_generic(lab), lab)
        for lab in ("muscle MRI fat fraction", "6-minute walk test", "CRISPR/Cas9 genome editing",
                    "filipin staining", "urinary hexose tetrasaccharide"):
            self.assertFalse(is_generic(lab), lab)
        self.assertFalse(is_generic("GAA sequencing", {"gaa"}))

    def test_penalties(self):
        ents = {"d": ("MONDO:1", "Pompe disease", "disease"),
                "s": ("text:method:sanger", "Sanger sequencing", "method"),
                "o": ("MONDO:2", "myelofibrosis", "disease"),
                "g": ("HGNC:1", "ACVR1", "gene"),
                "e": ("text:outcome_measure:svr", "spleen volume reduction", "outcome_measure")}
        recs = [rec("P1", ents, [("s", "diagnoses", "d", "d")], level="observational"),
                rec("P2", ents, [("e", "measures_outcome_of", "o"), ("o", "causes", "g"),
                                 ("d", "causes", "g")], level="clinical_trial")]
        prof = profile(genes=[{"id": "HGNC:1", "label": "ACVR1", "causal": True}])
        g, _ = build(prof, recs, "all")
        cs = {c["id"]: c for c in g.candidates()}
        self.assertTrue(cs["text:method:sanger"]["generic"])
        self.assertEqual(cs["text:method:sanger"]["score"], round(0.7 * 0.8 * 0.3, 3))
        e = cs["text:outcome_measure:svr"]
        self.assertEqual(e["category"], "transfer")
        self.assertTrue(e["other_disease_endpoint"])
        g, _ = build(prof, recs, "none")
        cs = {c["id"]: c for c in g.candidates()}
        self.assertNotIn("generic", cs["text:method:sanger"])
        self.assertEqual(cs["text:method:sanger"]["score"], 0.7)


class NonSolutionTests(Base):
    def test_rules(self):
        cases = {"prospective open-label trial": "method",
                 "randomized, double-blind, placebo-controlled": "method",
                 "Fisher's Exact test": "method", "Inverse Kaplan-Meier method": "method",
                 "generalized estimating equation": "method", "LUMINA-1": "resource",
                 "OPTIMA trial": "resource", "NCT02190747": "resource",
                 "ClinicalTrials.gov identifier": "resource",
                 "Bardet–Biedl syndrome patients": "model_system"}
        for lab, kind in cases.items():
            self.assertTrue(is_non_solution(lab, kind), lab)
        for lab, kind in {"InterRett": "resource", "Rett Syndrome Natural History Study":
                          "resource", "Sleep Disturbances Scale for Children": "outcome_measure",
                          "patient-derived fibroblasts": "model_system",
                          "Primary dermal fibroblasts derived from Rett patients":
                          "model_system", "GSE48452": "resource", "HEK293": "model_system",
                          "6-minute walk test": "outcome_measure", "TSHA-102": "therapy",
                          "developmental regression": "outcome_measure",
                          "International CDKL5 Disorder Database cohort": "resource"}.items():
            self.assertFalse(is_non_solution(lab, kind), lab)
        self.assertTrue(is_generic("high-performance liquid chromatography-mass spectrometry"))
        self.assertTrue(is_generic("LC-MS/MS"))

    def test_excluded_from_ranking_kept_in_graph(self):
        ents = {"d": ("MONDO:1", "Pompe disease", "disease"),
                "f": ("text:method:fisher", "Fisher's exact test", "method"),
                "t": ("text:resource:optima", "OPTIMA trial", "resource"),
                "x": ("text:drug:x", "drugx", "drug")}
        recs = [rec("P1", ents, [("f", "applicable_to", "d"), ("t", "tested_in", "d", "d"),
                                 ("x", "treats", "d", "d")], level="clinical_trial")]
        g, _ = build(profile(), recs, "all")
        ids = [c["id"] for c in g.candidates()]
        self.assertEqual(ids, ["text:drug:x"])
        self.assertEqual({x["id"] for x in g.non_solutions},
                         {"text:method:fisher", "text:resource:optima"})
        self.assertIn("text:method:fisher", g.nodes)
        g, _ = build(profile(), recs, "none")
        self.assertEqual(len(g.candidates()), 3)

    def test_extract_prompt(self):
        self.assertIn("NOT solution entities", extract.system())
        improvements.configure("none")
        self.assertIs(extract.system(), extract.SYSTEM)
        self.assertNotIn("NOT solution entities", extract.SYSTEM)


class ApprovedDrugTests(Base):
    def test_two_reviews_and_registry_tiers(self):
        ents = {"d": ("MONDO:1", "Pompe disease", "disease"),
                "t": ("text:drug:trof", "trofinetide", "drug"),
                "m": ("text:drug:mouse", "drugm", "drug"), "g": ("HGNC:1", "GAA", "gene")}
        recs = [rec(f"R{i}", ents, [("t", "treats", "d", "d")], level="review")
                for i in range(2)]
        recs.append(rec("M1", ents, [("m", "rescues", "g", "d")], level="animal"))
        g, _ = build(profile(), recs, "all")
        cs = {c["id"]: c for c in g.candidates()}
        self.assertEqual(cs["text:drug:trof"]["tier"], 2)
        self.assertEqual(cs["text:drug:mouse"]["tier"], 1)
        reg = rec("OT:x", ents, [("t", "tested_in", "d", "d")], level="registered_trial",
                  origin="registry")
        reg["edges"][0]["evidence"][0]["quote"] = "Open Targets lists trofinetide (max stage: " \
                                                   "approval)"
        g, _ = build(profile(), recs + [reg], "all")
        cs = {c["id"]: c for c in g.candidates()}
        self.assertEqual(cs["text:drug:trof"]["tier"], 3)
        self.assertGreater(cs["text:drug:trof"]["score"], cs["text:drug:mouse"]["score"])

    def test_parents_and_registry_queries(self):
        graph = {"start": "MONDO:2", "focus": ["MONDO:2"], "edges": [
            {"from": "MONDO:2", "to": "MONDO:3", "relation": "subclass_of", "source": "x"},
            {"from": "MONDO:3", "to": "CHEMBL:1", "relation": "approved_drug", "source": "x"}],
            "nodes": [{"id": "MONDO:2", "label": "Atypical Rett syndrome", "kind": "disease",
                       "xrefs": ["ORPHA:3095"], "info": {}},
                      {"id": "MONDO:3", "label": "Rett syndrome", "kind": "disease",
                       "xrefs": ["ORPHA:778"], "info": {"synonyms": ["RTT"]}},
                      {"id": "MONDO:4", "label": "Rett-like syndrome", "kind": "disease",
                       "xrefs": [], "info": {}},
                      {"id": "CHEMBL:1", "label": "TROFINETIDE", "kind": "drug", "xrefs": [],
                       "info": {}}]}
        with patch("quality.specificity", return_value=0.5):
            prof = build_profile(graph, {})
        self.assertEqual([p["id"] for p in prof.identity], ["MONDO:3"])
        self.assertEqual([d["label"] for d in prof.drugs], ["TROFINETIDE"])

        class Reg:
            errors = []

            def __init__(self):
                self.asked = []

            def disease_trials(self, p):
                self.asked.append(p.disease["label"])
                return [{"nct": "NCT1", "title": "t", "status": "s", "phases": [],
                         "type": "INTERVENTIONAL", "start": "2020", "conditions": [],
                         "summary": "", "interventions": [
                             {"name": "trofinetide", "type": "drug", "other_names": []}]}]

            def disease_drugs(self, p):
                return []

        class Norm:
            def resolve_many(self, ents, title=""):
                return {k: {"id": "text:drug:t", "label": e["name"], "how": "text",
                            "xrefs": []} for k, e in ents.items()}
        r = Reg()
        recs, raw = registry_records(r, prof, Norm())
        self.assertEqual(r.asked, ["Atypical Rett syndrome", "Rett syndrome"])
        self.assertEqual(len(recs), 1)  # NCT1 once
        improvements.configure("none")
        with patch("quality.specificity", return_value=0.5):
            prof = build_profile(graph, {})
        self.assertEqual((prof.identity, prof.drugs), ([], []))

    def test_reserved_known_drug_paper(self):
        papers = [{"pmid": str(i), "cited_by": 100 - i} for i in range(6)]
        dec = {f"PMID:{i}": {"include": True, "relevance": 3, "solutions": [f"sol{i}"],
                             "solution_types": ["drug"]} for i in range(6)}
        dec["PMID:5"]["solutions"] = ["trofinetide", "blarcamesine"]
        dec["PMID:5"]["relevance"] = 2
        sel = screen.select(papers, dec, 2, 3, known=["TROFINETIDE"])
        self.assertIn("5", [p["pmid"] for p in sel])
        improvements.configure("none")
        self.assertNotIn("5", [p["pmid"] for p in screen.select(papers, dec, 2, 3,
                                                                known=["TROFINETIDE"])])


class SymptomAxisTests(Base):
    def test_typed_symptom_bridge(self):
        graph = {"query": {"mode": "candidates", "parts": [
            {"text": "heterotopic ossification", "kind": "phenotype", "id": "HP:0011986",
             "label": "Ectopic ossification"}, {"text": "x", "kind": "unknown"}]}}
        sy = typed_symptoms(graph)
        self.assertEqual(sy[0]["names"], ["Ectopic ossification", "heterotopic ossification"])
        self.assertEqual(typed_symptoms({"query": {"mode": "disease"}}), [])
        ents = {"n": ("text:drug:nsaid", "indomethacin", "drug"),
                "h": ("text:process:ho", "heterotopic ossification", "process")}
        recs = [rec("P1", ents, [("n", "treats", "h")], level="clinical_trial")]
        prof = profile(symptoms=sy)
        g, _ = build(prof, recs, "all")
        c = g.candidates()[0]
        self.assertEqual(c["paths"][0]["bridge_weight"], 0.7)
        self.assertIn("typed input symptom", c["paths"][0]["bridge"])
        g, _ = build(prof, recs, "none")
        self.assertEqual(g.candidates(), [])  # no bridge to the input without the axis

    def test_symptom_queries(self):
        graph = {"start": "term:x", "focus": [], "edges": [],
                 "nodes": [{"id": "HP:1", "label": "Cataplexy", "kind": "phenotype",
                            "xrefs": [], "info": {"synonyms": ["cataplectic attacks"]}}],
                 "query": {"mode": "candidates", "parts": [
                     {"text": "cataplexy", "kind": "phenotype", "id": "HP:1",
                      "label": "Cataplexy"},
                     {"text": "FBN1", "kind": "gene", "id": "FBN1"}]}}
        qs = lit_plan.symptom_queries(graph, {n["id"]: n for n in graph["nodes"]}, None)
        self.assertEqual([q.relation for q in qs], ["symptom_treatment"])
        self.assertEqual(qs[0].disease.names[0], "Cataplexy")
        from literature.europepmc import EuropePmcProvider
        term, _ = EuropePmcProvider(None).term(qs[0])
        self.assertIn('TITLE_ABS:"Cataplexy"', term)
        self.assertIn('TITLE_ABS:"treatment"', term)
        improvements.configure("-symptom_axis")
        self.assertEqual([q for q in lit_plan.plan(graph, None)
                          if q.relation == "symptom_treatment"], [])


class GapCheckTests(Base):
    class Reg:
        def __init__(self, counts=None):
            self.counts, self.calls = counts or {}, []

        def cooccurrence(self, a, b, n=5, also=()):
            self.calls.append((list(a), list(also)))
            return {"count": self.counts.get(a[0], 0), "papers": [], "query": a[0]}

        def trials_of(self, names, prof):
            return []

    def test_core_names(self):
        head, product = gaps.core_names(
            "cerebellomedullary-cistern injection of adeno-associated viral vector "
            "serotype 9 encoding human acid sphingomyelinase")
        self.assertEqual((head, product), ("adeno-associated", "acid sphingomyelinase"))
        qs = gaps.core_queries({"intervention_label": "activin A neutralizing monoclonal "
                                                      "antibody", "mechanism": "X"}, set())
        self.assertEqual(qs[0]["names"], ["activin a"])
        self.assertIn("antibody", qs[0]["also"])

    def test_tested_in_graph_and_merged_salts(self):
        prof = profile(genes=[{"id": "HGNC:1", "label": "GAA", "causal": True}])
        ents = {"d": ("MONDO:1", "Pompe disease", "disease"), "g": ("HGNC:1", "GAA", "gene"),
                "o": ("MONDO:7", "other storage disease", "disease"),
                "a": ("text:therapy:aav", "adeno-associated viral gene therapy", "therapy"),
                "t": ("text:therapy:act", "ACTUS-101 AAV gene therapy", "therapy"),
                "m1": ("text:drug:mom", "momelotinib", "drug"),
                "m2": ("ChEMBL:5", "MOMELOTINIB DIHYDROCHLORIDE MONOHYDRATE", "drug")}
        recs = [rec("P1", ents, [("d", "causes", "g"), ("o", "causes", "g"),
                                 ("a", "treats", "o"), ("m1", "treats", "o")]),
                rec("P2", ents, [("t", "treats", "d", "d")], level="clinical_trial"),
                rec("P3", {**ents, "m1": ents["m2"]}, [("m1", "rescues", "o")])]
        improvements.configure("none")  # m1 / m2 stay apart in the graph ...
        g = Graph(prof)
        for r in recs:
            g.add_paper(r)
        g.merge_duplicates()
        g.score_edges()
        cands = g.candidates()
        improvements.configure("all")  # ... and only the gap pass merges them
        out = gaps.find(g, cands, self.Reg(), prof)
        by = {x["intervention_label"]: x for x in out}
        self.assertEqual(by["adeno-associated viral gene therapy"]["status"], "tested")
        self.assertIn("ACTUS-101", by["adeno-associated viral gene therapy"]["reason"])
        moms = [x for x in out if "momelotinib" in x["intervention_label"].lower()]
        self.assertEqual(len(moms), 1)
        self.assertEqual(len(moms[0]["merged_interventions"]), 1)
        improvements.configure("none")
        out = gaps.find(g, cands, self.Reg(), prof)
        self.assertEqual(len([x for x in out
                              if "momelotinib" in x["intervention_label"].lower()]), 2)
        self.assertTrue(all("reason" not in x for x in out))
        self.assertEqual({x["status"] for x in out}, {"untested"})

    def test_core_count_decides(self):
        prof = profile()
        ents = {"d": ("MONDO:1", "Pompe disease", "disease"), "g": ("HGNC:2", "SMPD1", "gene"),
                "o": ("MONDO:7", "other disease", "disease"),
                "a": ("text:therapy:c", "intracisternal injection of AAV9 vector encoding "
                                        "human acid sphingomyelinase", "therapy")}
        recs = [rec("P1", ents, [("d", "involves", "g"), ("o", "involves", "g"),
                                 ("a", "treats", "o")])]
        g, _ = build(prof, recs, "all")
        reg = self.Reg({"AAV9": 12})
        out = gaps.find(g, g.candidates(), reg, prof)
        self.assertEqual(out[0]["status"], "tested")
        self.assertIn("core intervention", out[0]["reason"])
        self.assertTrue(any(c[1] == ["acid sphingomyelinase"] for c in reg.calls))


class QueryHygieneTests(Base):
    def test_sanitize(self):
        self.assertEqual(sanitize_terms(["Alstrom\\", 'Alstrom "x"', "ab", "a; b c d",
                                         "Alström syndrome", "ALSTROM\\"]),
                         ["Alstrom", "Alstrom x", "Alström syndrome"])

    def test_epmc_query_and_exclusions(self):
        prof = profile(disease={"names": ["Alstrom\\", "Alström syndrome", "AS"]})
        q = transfer.epmc_query([["MC4R"], ["agonist"]], transfer.exclusions(prof))
        self.assertNotIn("\\", q)
        self.assertIn('NOT (TITLE_ABS:"Alstrom" OR TITLE_ABS:"Alström syndrome")', q)
        improvements.configure("none")
        self.assertIn("\\", transfer.epmc_query([["MC4R"]], transfer.exclusions(prof)))

    def test_retry_without_setting_group(self):
        class Epmc:
            def __init__(self):
                self.queries = []

            def _search(self, q, n):
                self.queries.append(q)
                if "TITLE_ABS:\"Bardet-Biedl\"" in q:
                    return []
                return [{"pmid": "1", "title": "t", "abstractText": "a"}]

            def fail(self, what, e):
                raise AssertionError(what)
        mech = {"id": "HGNC:1", "label": "MC4R", "searches": [
            {"description": "d", "groups": [["MC4R"], ["agonist"], ["Bardet-Biedl"]]}]}
        ep = Epmc()
        papers, log = transfer.search(ep, mech, profile(), 25)
        self.assertEqual(len(ep.queries), 2)
        self.assertEqual(len(papers), 1)
        self.assertTrue(log[1]["retry"])
        improvements.configure("none")
        ep = Epmc()
        papers, log = transfer.search(ep, mech, profile(), 25)
        self.assertEqual((len(ep.queries), papers), (1, []))

    def test_registry_names(self):
        reg = Registries(None)
        seen = []
        with patch.object(reg, "_live", side_effect=lambda url, p: seen.append(p) or {}):
            reg.trials_of(["setmelanotide\\"], profile(disease={"names": ["Alstrom\\",
                                                                          "Alström syndrome"]}))
        self.assertNotIn("\\", seen[0]["query.cond"] + seen[0]["query.intr"])


class SymptomAnchorTests(Base):
    class Hpo:
        exact = {"cone rod dystrophy": "HP:0000548"}
        names = {"HP:0000548": "Cone/cone-rod dystrophy",
                 "HP:0012171": "Stereotypical hand wringing"}
        dname = {}

        def resolve(self, text):
            hp = self.exact.get(" ".join(text.lower().split()))
            return (hp, self.names[hp], "exact") if hp else None

        def descendants(self, root):
            return set(self.names)

        def suggest(self, text, n=3):
            return [("HP:0012171", "Stereotypical hand wringing", "hand-wringing")]

        def gene_symbol(self, t):
            return None

    @staticmethod
    def disease(text, hpo):
        if text != "cone-rod dystrophy":
            return None
        return resolve.Part(text, "disease", "ORPHA:1872", "cone-rod dystrophy", "name",
                            alternative="Disease")

    def test_phenotype_reading_under_symptoms(self):
        with patch.object(resolve, "_disease", self.disease):
            p = resolve.read_part("cone-rod dystrophy", self.Hpo(), prefer_phenotype=True)
            self.assertEqual((p.kind, p.id), ("phenotype", "HP:0000548"))
            p = resolve.read_part("stereotypic hand wringing", self.Hpo(),
                                  prefer_phenotype=True)
            self.assertEqual((p.kind, p.id), ("phenotype", "HP:0012171"))
            improvements.configure("none")
            p = resolve.read_part("cone-rod dystrophy", self.Hpo(), prefer_phenotype=True)
            self.assertEqual(p.kind, "disease")

    def test_dedupe_and_gate(self):
        def r(i, name, score, full, xrefs=()):
            return {"id": i, "name": name, "score": score, "xrefs": list(xrefs), "genes": [],
                    "matches": [("HP:%d" % k, "HP:%d" % k, k < full, 1.0) for k in range(4)]}
        ranking = [r("ORPHA:64", "Alström syndrome", 13.1, 4),
                   r("OMIM:203800", "Alstrom syndrome", 12.0, 3),
                   r("OMIM:1", "Close disease", 12.0, 3),
                   r("OMIM:2", "Far disease", 10.8, 2),
                   r("OMIM:3", "Low disease", 9.0, 4)]
        with patch.object(resolve, "orphanet_ids", return_value=[]), \
                patch.object(resolve, "_mondo_of", return_value=[]):
            dd = resolve.dedupe_ranking(ranking)
        self.assertEqual([x["id"] for x in dd], ["ORPHA:64", "OMIM:1", "OMIM:2", "OMIM:3"])
        self.assertIn("OMIM:203800", dd[0]["xrefs"])
        chosen = resolve.focus_gate(dd, 3, 4)
        self.assertEqual([x["id"] for x in chosen], ["ORPHA:64", "OMIM:1"])
        pinned = {**r("ORPHA:9", "Named", 0.0, 0), "pinned": True}
        self.assertEqual([x["id"] for x in resolve.focus_gate([pinned] + dd, 3, 4)],
                         ["ORPHA:9", "ORPHA:64", "OMIM:1"])

    def test_focus_dedup_after_merge(self):
        start = Node("a; b", kind="query")
        a = Node("Disease A", id="OMIM:1", kind="disease")
        b = Node("Disease B", id="OMIM:2", kind="disease")
        seed = [Edge(start, a, "candidate_disease", "hpoa"),
                Edge(start, b, "candidate_disease", "hpoa")]

        class Src:
            name, focus_cap = "fake", None

            def accepts(self, node):
                return True

            def ids_for(self, node):
                return [node.id]

            def query(self, node, limit=5):
                return [Edge(node, Node("Disease B", id="OMIM:2", kind="disease"), "xref",
                             "fake")] if node.id == "OMIM:1" else []

        def focus_after(spec):
            improvements.configure(spec)
            ents, stats = Entities(), Stats()
            stats.focus_keys = ["OMIM:1", "OMIM:2"]
            with patch("quality.is_generic", return_value=False):
                main.run(start, [Src()], 1, 5, 50, ents, stats, seed=seed,
                         focus_candidates=3)
            return stats.focus, ents
        focus, ents = focus_after("all")
        self.assertEqual(len(focus), 1)
        focus, ents = focus_after("none")
        self.assertEqual(len(focus), 2)
        self.assertEqual(len({ents.find(f) for f in focus}), 1)


class DiverseSelectionTests(Base):
    def test_round_robin_over_solutions(self):
        papers = [{"pmid": str(i), "cited_by": 100 - i} for i in range(5)]
        dec = {f"PMID:{i}": {"include": True, "relevance": 3, "solutions": ["ERT"],
                             "solution_types": ["therapy"]} for i in range(4)}
        dec["PMID:4"] = {"include": True, "relevance": 3, "solutions": ["AAV gene therapy"],
                         "solution_types": ["therapy"]}
        sel = screen.select(papers, dec, 2, 2)
        self.assertEqual([p["pmid"] for p in sel], ["0", "4"])
        dec["PMID:3"]["relevance"] = 2  # a lower level never goes before a higher one
        dec["PMID:3"]["solutions"] = ["miglustat"]
        self.assertEqual([p["pmid"] for p in screen.select(papers, dec, 2, 3)],
                         ["0", "4", "1"])
        improvements.configure("none")
        self.assertEqual([p["pmid"] for p in screen.select(papers, dec, 2, 2)], ["0", "1"])


if __name__ == "__main__":
    unittest.main()
