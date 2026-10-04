"""Output quality of the first graph against GA4GH phenopacket-store patients
(src/query-test/phenobench.py).

PhenobenchUnit      offline: the benchmark itself on a tiny synthetic HPO (parsing,
                    sampling, identity, leave-publication-out holdout, perturbation,
                    excluded-term contradictions, metrics, profile scoring)
RankingQuality      opt-in (PHENOBENCH=1, ~2 min): ranks a fixed sample of 40 patients
                    (one per disease) as main.py does and checks top-k floors, also with
                    the annotation_coverage improvement off and leave-one-patient-out; needs
                    data/phenopackets/all_phenopackets.zip (phenobench.py download) and
                    data/hpo
ProfileQuality      the focus profiles of finished runs (runs/*.json, gitignored) against
                    the patients of their disease; skipped when the runs or the store are
                    missing. expectedFailure marks known gaps: they start "unexpectedly
                    passing" once fixed.

Floors are set a little below the numbers measured on 2026-10-04 (see the asserts), so a
regression fails and an improvement can raise them.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HAS_REQUESTS = importlib.util.find_spec("requests") is not None
if HAS_REQUESTS:
    sys.path.insert(0, str(ROOT / "src" / "query-test"))
    try:
        import improvements
        import phenobench as pb
        import rerank  # noqa: F401  (imported here, while src/query-test is on the path)
        import resolve
        from sources import _hpoa
    finally:
        sys.path.pop(0)

# -- a tiny HPO ------------------------------------------------------------------------
ALL, PHEN, INH = "HP:0000001", "HP:0000118", "HP:0000005"
EYE, LENS = "HP:9000010", "HP:9000011"  # Abnormality of the eye > Ectopia lentis
SKEL, ARACH, SCOL = "HP:9000020", "HP:9000021", "HP:9000022"
HEART, ANEUR = "HP:9000030", "HP:9000031"
ALPHA, BETA, GAMMA = "OMIM:900001", "OMIM:900002", "OMIM:900003"


def mini_hpo():
    names = {ALL: "All", PHEN: "Phenotypic abnormality", INH: "Mode of inheritance",
             EYE: "Abnormality of the eye", LENS: "Ectopia lentis",
             SKEL: "Abnormality of the skeleton", ARACH: "Arachnodactyly", SCOL: "Scoliosis",
             HEART: "Abnormality of the heart", ANEUR: "Aortic aneurysm"}
    parents = {ALL: (), PHEN: (ALL,), INH: (ALL,), EYE: (PHEN,), LENS: (EYE,),
               SKEL: (PHEN,), ARACH: (SKEL,), SCOL: (SKEL,), HEART: (PHEN,),
               ANEUR: (HEART,)}
    ann = {ALPHA: {LENS: 0.9, ARACH: 0.9, ANEUR: 0.9},
           BETA: {SCOL: 1.0, EYE: 0.5},
           GAMMA: {ANEUR: 0.3, SCOL: 0.3}}
    d = {"name": names, "parents": parents, "alt": {},
         "exact": {v.lower(): k for k, v in names.items()}, "related": {},
         "ann": ann, "dname": {ALPHA: "Alpha syndrome", BETA: "Beta syndrome",
                               GAMMA: "Gamma syndrome"},
         "count": {}, "refs": {(ALPHA, LENS): ("PMID:1",), (ALPHA, ARACH): ("PMID:1", "PMID:2")},
         "g2d": {"GENEA": [(ALPHA, "MENDELIAN")], "GENEB": [(BETA, "MENDELIAN")]},
         "gene_ids": {"NCBIGene:1": "GENEA", "NCBIGene:2": "GENEB"}}
    tmp = _hpoa.HpoData(d)
    for dis in ann:
        for t in tmp._propagated(dis):
            d["count"][t] = d["count"].get(t, 0) + 1
    return _hpoa.HpoData(d)


def packet(pid, disease, label, observed=(), excluded=(), gene=None, hgvs_g=None,
           pmid="PMID:1"):
    feats = [{"type": {"id": t}} for t in observed] + \
            [{"type": {"id": t}, "excluded": True} for t in excluded]
    gi = []
    if gene:
        vd = {"geneContext": {"symbol": gene},
              "expressions": [{"syntax": "hgvs.c", "value": "NM_1.1:c.1A>G"}]
              + ([{"syntax": "hgvs.g", "value": hgvs_g}] if hgvs_g else [])}
        gi.append({"variantInterpretation": {"variationDescriptor": vd}})
    return {"id": pid, "phenotypicFeatures": feats,
            "metaData": {"externalReferences": [{"id": pmid}]},
            "interpretations": [{"diagnosis": {"disease": {"id": disease, "label": label},
                                               "genomicInterpretations": gi}}]}


def case(pid="P1", disease=ALPHA, label="Alpha syndrome", observed=(LENS, ARACH, ANEUR),
         excluded=(), genes=(), pmid="PMID:1", variants=()):
    return pb.Case(pid, "COHORT", pmid, disease, label, tuple(observed), tuple(excluded),
                   tuple(genes), tuple(variants))


@unittest.skipUnless(HAS_REQUESTS, "requests not installed")
class PhenobenchUnit(unittest.TestCase):
    def setUp(self):
        improvements.configure("all")
        self.db = patch.object(resolve, "_db", lambda name: None)  # no local indexes
        self.db.start()
        pb._keys.cache_clear()
        pb.mondo_family.cache_clear()
        pb.mondo_descendants.cache_clear()
        self.hpo = mini_hpo()

    def tearDown(self):
        self.db.stop()
        pb._keys.cache_clear()
        improvements.configure(None)

    def test_parse(self):
        p = packet("PMID_1_P1", ALPHA, "Alpha syndrome", [LENS, ARACH], [ANEUR], "geneA",
                   "NC_000001.11:g.5A>G")
        (c,) = pb.parse(p, "GENEA")
        self.assertEqual((c.disease_id, c.pmid, c.cohort), (ALPHA, "PMID:1", "GENEA"))
        self.assertEqual((c.observed, c.excluded), ((LENS, ARACH), (ANEUR,)))
        self.assertEqual((c.genes, c.variants), (("GENEA",), ("NC_000001.11:g.5A>G",)))
        # no interpretation: the diseases block
        p = {"id": "x", "phenotypicFeatures": [{"type": {"id": LENS}}],
             "diseases": [{"term": {"id": BETA, "label": "Beta syndrome"}}]}
        (c,) = pb.parse(p)
        self.assertEqual((c.disease_id, c.genes, c.pmid), (BETA, (), None))

    def test_load_and_sample(self):
        with tempfile.TemporaryDirectory() as d:
            for cohort, pid, dis in [("GENEA", "a1", ALPHA), ("GENEA", "a2", ALPHA),
                                     ("GENEA", "a3", ALPHA), ("GENEB", "b1", BETA)]:
                f = Path(d, cohort, pid + ".json")
                f.parent.mkdir(exist_ok=True)
                f.write_text(json.dumps(packet(pid, dis, dis, [LENS])), encoding="utf-8")
            cases = pb.load_cases(Path(d))
            self.assertEqual(len(cases), 4)
            self.assertEqual(len(pb.load_cases(Path(d), ["geneb"])), 1)
        s = pb.sample(cases, per_disease=2, max_cases=None)
        self.assertEqual(sorted(c.disease_id for c in s), [ALPHA, ALPHA, BETA])
        self.assertEqual(s, pb.sample(cases, per_disease=2, max_cases=None))  # seeded
        # max_cases drops whole diseases (macro averages stay fair), never splits one
        for seed in range(5):
            capped = pb.sample(cases, per_disease=2, max_cases=2, seed=seed)
            self.assertIn(sorted(c.disease_id for c in capped), ([ALPHA, ALPHA], [BETA]))
        # a patient with no observed term is not rankable
        empty = pb.Case("e", "", None, GAMMA, "", (), (LENS,))
        self.assertNotIn(empty, pb.sample(cases + [empty], None, None))

    def test_rank_and_identity(self):
        r = pb.rank_case(case(), self.hpo, holdout_on=False)
        self.assertEqual((r["rank"], r["n_terms"]), (1, 3))
        self.assertTrue(r["top1"].startswith("Alpha syndrome"))
        # an entry that carries the diagnosis as an xref (merged OMIM/ORPHA) is a hit
        truth = pb.truth_keys(case())
        ranking = [{"id": "ORPHA:5", "name": "Other", "xrefs": []},
                   {"id": "ORPHA:6", "name": "Alpha (Orphanet)", "xrefs": [ALPHA]}]
        self.assertEqual(pb.find_rank(ranking, truth), 2)
        self.assertIsNone(pb.find_rank(ranking[:1], truth))

    def test_gene_input(self):
        c = case(observed=(), genes=("GENEB",), disease=BETA, label="Beta syndrome")
        self.assertEqual(pb.rank_case(c, self.hpo, input="gene")["rank"], 1)
        self.assertEqual(pb.rank_case(c, self.hpo, input="hpo").get("skipped"),
                         "nothing to search with")

    def test_holdout(self):
        c = case(observed=(LENS,))
        with pb.holdout(self.hpo, c) as hidden:
            self.assertEqual(hidden, 1)  # LENS cites only PMID:1; ARACH cites two papers
            self.assertNotIn(LENS, self.hpo.ann[ALPHA])
            self.assertIn(ARACH, self.hpo.ann[ALPHA])
            self.assertNotIn(ALPHA, self.hpo.by_term[LENS])
        self.assertEqual(self.hpo.ann[ALPHA][LENS], 0.9)  # restored
        self.assertIn(ALPHA, self.hpo.by_term[LENS])
        # the patient's own paper was the only evidence: without it the lens sign
        # points to Beta (abnormality of the eye) instead
        self.assertEqual(pb.rank_case(c, self.hpo, holdout_on=False)["rank"], 1)
        held = pb.rank_case(c, self.hpo)
        self.assertNotEqual(held["rank"], 1)
        self.assertEqual(held["hidden"], 1)
        with pb.holdout(self.hpo, case(pmid="PMID:3")) as hidden:  # other paper
            self.assertEqual(hidden, 0)

    def test_holdout_patient(self):
        """Leave-one-patient-out: the cohort count of an own-paper annotation is
        recomputed without the patient instead of the annotation being dropped."""
        counts = {(ALPHA, LENS): (3, 4)}
        with patch.object(pb, "_cohort_counts", lambda: counts):
            with pb.holdout(self.hpo, case(observed=(LENS,)), "patient") as changed:
                self.assertEqual(changed, 1)
                self.assertAlmostEqual(self.hpo.ann[ALPHA][LENS], 2 / 3)  # had it
                self.assertIn(ALPHA, self.hpo.by_term[LENS])
            self.assertEqual(self.hpo.ann[ALPHA][LENS], 0.9)
            with pb.holdout(self.hpo, case(observed=(ARACH,), excluded=(LENS,)), "patient"):
                self.assertAlmostEqual(self.hpo.ann[ALPHA][LENS], 1.0)  # lacked it: 3/3
            counts[(ALPHA, LENS)] = (1, 1)  # the patient was the whole cohort
            with pb.holdout(self.hpo, case(observed=(LENS,)), "patient"):
                self.assertNotIn(LENS, self.hpo.ann[ALPHA])
            with pb.holdout(self.hpo, case(), "none") as changed:
                self.assertEqual(changed, 0)
        self.assertEqual(self.hpo.ann[ALPHA][LENS], 0.9)

    def test_annotation_coverage(self):
        """A disease whose annotations the query explains fully keeps its score; one
        with unexplained annotations is scaled down (improvement annotation_coverage)."""
        h = self.hpo
        qanc = {a: h.ic(a) for q in (ANEUR, SCOL) for a in h.ancestors(q)}
        self.assertAlmostEqual(h.coverage(GAMMA, qanc), 1.0)
        self.assertLess(h.coverage(ALPHA, qanc), 0.5)  # lens and fingers unexplained
        plain = {r["id"]: r["score"] for r in h.rank_diseases([ANEUR, SCOL])}
        cov = {r["id"]: r["score"] for r in h.rank_diseases([ANEUR, SCOL], coverage=resolve.COVERAGE)}
        self.assertAlmostEqual(cov[GAMMA], plain[GAMMA])
        self.assertLess(cov[ALPHA], plain[ALPHA])
        # the switch: resolve.rank passes the factor only with the improvement on
        interp = resolve.Interpretation("q", [resolve.Part(t, "phenotype", t)
                                              for t in (ANEUR, SCOL)])
        on = {r["id"]: r["score"] for r in resolve.rank(interp, 5, hpo=h)}
        improvements.configure("all,-annotation_coverage")
        off = {r["id"]: r["score"] for r in resolve.rank(interp, 5, hpo=h)}
        self.assertAlmostEqual(on[ALPHA], cov[ALPHA])
        self.assertAlmostEqual(off[ALPHA], plain[ALPHA])

    def test_llm_rerank(self):
        import rerank
        ranking = [{"id": i, "name": n, "xrefs": [], "score": 1.0, "matches": [], "genes": []}
                   for i, n in ((BETA, "Beta syndrome"), (GAMMA, "Gamma syndrome"),
                                (ALPHA, "Alpha syndrome"))]
        system, user = rerank.prompt(ranking, [LENS, ARACH], ["GENEA"], self.hpo, [SCOL])
        self.assertIn("3. Alpha syndrome (OMIM:900001); genes: GENEA", user)
        self.assertIn("Ectopia lentis (very frequent)", user)
        self.assertIn("explicitly absent: Scoliosis", user)
        # the model's order; junk and repeats ignored, left-out candidates keep order
        out = rerank.apply(ranking, {"order": [3, "x", 3, 99]}, n=3)
        self.assertEqual([r["id"] for r in out], [ALPHA, BETA, GAMMA])
        self.assertEqual([r["tool_rank"] for r in out], [3, 1, 2])
        self.assertIs(rerank.apply(ranking, {"order": []}), ranking)
        self.assertEqual([r["id"] for r in rerank.apply(ranking, {"order": [2]}, n=1)],
                         [BETA, GAMMA, ALPHA])  # only the top n are re-ordered

        class Fake:
            def __init__(self, reply=None, fail=False):
                self.reply, self.fail, self.calls = reply, fail, 0

            def chat(self, system, user, **kw):
                self.calls += 1
                if self.fail:
                    raise RuntimeError("down")
                return self.reply
        pinned = [{**ranking[1], "pinned": True}, ranking[0], ranking[2]]
        out = rerank.rerank(pinned, [LENS], [], self.hpo, llm=Fake({"order": [2, 1]}))
        self.assertEqual([r["id"] for r in out], [GAMMA, ALPHA, BETA])  # pinned stays
        with patch("sys.stderr"):
            self.assertIs(rerank.rerank(ranking, [LENS], [], self.hpo, llm=Fake(fail=True)),
                          ranking)
        fake = Fake()
        self.assertIs(rerank.rerank(ranking, [], ["GENEA"], self.hpo, llm=fake), ranking)
        self.assertEqual(fake.calls, 0)  # a gene-only search is not re-ordered
        # in the benchmark: the model's order is scored, the tool's order kept alongside
        c = case(observed=(ANEUR,))  # coverage puts Gamma (all explained) first
        r = pb.rank_case(c, self.hpo, holdout_on=False, llm=Fake({"order": [2, 1]}))
        self.assertEqual((r["tool_rank"], r["rank"]), (2, 1))
        self.assertEqual(pb.summarize([r])["tool_order"]["top1"], 0.0)

    def test_perturb(self):
        c = case()
        self.assertEqual(pb.perturb(c, self.hpo), [LENS, ARACH, ANEUR])
        two = pb.perturb(c, self.hpo, max_terms=2, seed=1)
        self.assertEqual(len(two), 2)
        self.assertEqual(two, pb.perturb(c, self.hpo, max_terms=2, seed=1))
        self.assertEqual(sorted(pb.perturb(c, self.hpo, imprecision=1.0)),
                         sorted([EYE, SKEL, HEART]))
        noisy = pb.perturb(c, self.hpo, noise=2)
        self.assertTrue(set(noisy) >= {LENS, ARACH, ANEUR})
        self.assertTrue(set(noisy) <= set(self.hpo.by_term) - {PHEN})

    def test_contradicted(self):
        # Alpha has ectopia lentis at 0.9: a patient without any eye abnormality
        # contradicts it; Gamma's aneurysm (0.3) is too rare to contradict
        self.assertEqual(pb.contradicted(self.hpo, [ALPHA], [EYE]), [EYE])
        self.assertEqual(pb.contradicted(self.hpo, [ALPHA], [SCOL]), [])
        self.assertEqual(pb.contradicted(self.hpo, [GAMMA], [ANEUR]), [])
        improvements.configure("all,-annotation_coverage")  # plain score: Alpha first
        r = pb.rank_case(case(observed=(ANEUR,), excluded=(LENS,), disease=GAMMA,
                              label="Gamma syndrome"), self.hpo, holdout_on=False)
        self.assertTrue(r["top1"].startswith("Alpha"))
        self.assertEqual((r["top1_contradicted"], r["truth_contradicted"]), ([LENS], []))
        self.assertEqual(pb.summarize([r])["rescuable"], 1)

    def test_summarize(self):
        rs = [{"disease": "X", "rank": 1, "hidden": 0},
              {"disease": "X", "rank": None, "hidden": 2},
              {"disease": "Y", "rank": 3, "hidden": 0, "top1_contradicted": ["HP:1"]},
              {"disease": "Z", "rank": None, "hidden": 0, "skipped": "nothing"}]
        s = pb.summarize(rs)
        self.assertEqual((s["cases"], s["diseases"], s["skipped"]), (3, 2, 1))
        self.assertAlmostEqual(s["micro"]["top1"], 1 / 3)
        self.assertAlmostEqual(s["macro"]["top1"], 0.25)
        self.assertAlmostEqual(s["micro"]["top3"], 2 / 3)
        self.assertAlmostEqual(s["macro"]["top3"], 0.75)
        self.assertAlmostEqual(s["micro"]["mrr"], (1 + 1 / 3) / 3)
        self.assertEqual((s["hidden_annotations"], s["rescuable"]), (2, 1))
        self.assertEqual(pb.summarize([])["cases"], 0)

    def test_score_profile(self):
        cohort = pb.Cohort([
            case("P1", observed=(LENS, ARACH), excluded=(ANEUR,), genes=("GENEA",),
                 variants=("g1",)),
            case("P2", observed=(LENS, SCOL), excluded=(HEART,), genes=("GENEB",)),
            case("P3", observed=(LENS,), excluded=(ANEUR,), genes=("GENEA",))], self.hpo)
        self.assertEqual((cohort.has(EYE), cohort.has(LENS), cohort.lacks(ANEUR)), (3, 3, 3))
        r = pb.score_profile({LENS: 0.9, ARACH: 0.9, ANEUR: 0.9}, {"GENEA"}, cohort,
                             graph_variants={"g1", "g9"})
        self.assertAlmostEqual(r["feature_recall"], 4 / 5)  # LENS x3, ARACH; not SCOL
        self.assertEqual(r["common_recall"], 1.0)  # only LENS is in >= 2 patients
        self.assertAlmostEqual(r["supported"], 2 / 3)  # ANEUR assessed, nobody has it
        self.assertEqual([t for t, *_ in r["contradicted"]], [ANEUR])
        self.assertEqual((r["gene_recall"], r["missing_genes"]), (0.5, ["GENEB"]))
        self.assertEqual(r["variant_recall"], 1.0)
        # a broader profile term: lenient only (if informative enough)
        r = pb.score_profile({SKEL: None}, set(), cohort, min_specificity=0.0)
        self.assertEqual(r["feature_recall"], 0.0)
        self.assertAlmostEqual(r["feature_recall_lenient"], 2 / 5)


# -- real data ---------------------------------------------------------------------------
HAVE_STORE = HAS_REQUESTS and pb.ZIP.exists() and (ROOT / "data" / "hpo" / "hp.obo").exists()


@unittest.skipUnless(HAVE_STORE and os.environ.get("PHENOBENCH") == "1",
                     "set PHENOBENCH=1 (and run phenobench.py download) for the ranking gate")
class RankingQuality(unittest.TestCase):
    """40 patients, one per disease, seed 0; leave-publication-out unless noted.
    Measured 2026-10-04 (annotation_coverage on): hpo top1/3/10 25/40/50% MRR 0.33
    (coverage off 25/35/45% MRR 0.31; no holdout 68/72/78%; leave-one-patient-out
    48/65/68% MRR 0.56), gene 57/85/95%, hpo+gene 75/92/98%. The language model
    re-ordering (llm_rerank) is not part of the gate: it needs a key and costs calls."""

    @classmethod
    def setUpClass(cls):
        improvements.configure("all")
        cls.hpo = _hpoa.load()
        cls.cases = pb.sample(pb.load_cases(), per_disease=1, max_cases=40, seed=0)
        cls.res = {inp: pb.summarize(pb.run_rank(cls.cases, cls.hpo, input=inp))
                   for inp in pb.INPUTS}
        cls.res["patient"] = pb.summarize(pb.run_rank(cls.cases, cls.hpo,
                                                      holdout_on="patient"))
        improvements.configure("all,-annotation_coverage")
        cls.res["plain"] = pb.summarize(pb.run_rank(cls.cases, cls.hpo))
        improvements.configure(None)

    def test_symptoms_only(self):
        s = self.res["hpo"]["micro"]
        self.assertGreaterEqual(s["top10"], 0.45, s)
        self.assertGreaterEqual(s["mrr"], 0.30, s)

    def test_annotation_coverage_helps(self):
        """The improvement must not lose against the plain score it switches off."""
        self.assertGreaterEqual(self.res["hpo"]["micro"]["mrr"],
                                self.res["plain"]["micro"]["mrr"])

    def test_new_patient_of_a_published_cohort(self):
        """Leave-one-patient-out: the cohort paper is known, only the patient is new."""
        s = self.res["patient"]["micro"]
        self.assertGreaterEqual(s["top10"], 0.60, s)
        self.assertGreaterEqual(s["mrr"], 0.50, s)
        self.assertGreater(s["mrr"], self.res["hpo"]["micro"]["mrr"])

    def test_gene_only(self):
        s = self.res["gene"]["micro"]
        self.assertGreaterEqual(s["top3"], 0.80, s)
        self.assertGreaterEqual(s["top10"], 0.90, s)

    def test_symptoms_and_gene(self):
        s = self.res["hpo+gene"]["micro"]
        self.assertGreaterEqual(s["top1"], 0.70, s)
        self.assertGreaterEqual(s["top3"], 0.88, s)
        # the patient's symptoms must sharpen a gene search, not blur it
        self.assertGreaterEqual(s["top1"], self.res["gene"]["micro"]["top1"])


RUNS = ROOT / "runs"


@unittest.skipUnless(HAVE_STORE and (RUNS / "marfan.json").exists(),
                     "needs data/phenopackets and the runs/ graphs")
class ProfileQuality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        improvements.configure(None)
        cls.hpo = _hpoa.load()
        cls.cases = pb.load_cases()

    def rows(self, run: str) -> dict[str, dict]:
        path = RUNS / run
        if not path.exists():
            self.skipTest(f"{run} missing")
        return {r["focus"]: r for r in pb.evaluate_run(path, self.cases, self.hpo)}

    def test_marfan_profile(self):
        """50 Marfan patients (FBN1): measured recall 92%, all common features, FBN1."""
        r = self.rows("marfan.json")["MONDO:0007947"]
        self.assertGreaterEqual(r["patients"], 30)
        self.assertGreaterEqual(r["feature_recall"], 0.85, r["missing_common"])
        self.assertGreaterEqual(r["common_recall"], 0.95, r["missing_common"])
        self.assertEqual(r["gene_recall"], 1.0)

    def test_gene_search_profile_matches_name_search(self):
        """The FBN1 search must give Marfan the same profile quality as the name search."""
        by_name = self.rows("marfan.json")["MONDO:0007947"]
        by_gene = self.rows("eval/marfan.gene.json")["MONDO:0007947"]
        self.assertEqual(by_gene["ranked"], 1)
        self.assertGreaterEqual(by_gene["feature_recall"], by_name["feature_recall"] - 0.03)

    def test_causal_genes_listed(self):
        """Every scored profile lists the causal gene of its own patients (measured: all
        but Rett in eval/rett.gene.json, see test_rett_identity)."""
        for run in ("marfan.json", "study/hgps.json", "study/pompe.json", "study/fop.json",
                    "study/npc.json"):
            for f, r in self.rows(run).items():
                if r["patients"]:
                    self.assertEqual(r["gene_recall"], 1.0, (run, f, r["missing_genes"]))

    @unittest.expectedFailure
    def test_hgps_hallmarks(self):
        """Known gap: short stature, lipodystrophy and low bone density (all 15 patients)
        never reach the graph: the per-source focus limit (40) cuts them; recall 39%."""
        r = self.rows("study/hgps.json")["MONDO:0008310"]
        missing = {name for _, name, _ in r["missing_common"]}
        self.assertFalse(missing & {"Short stature", "Lipodystrophy",
                                    "Reduced bone mineral density"}, missing)

    @unittest.expectedFailure
    def test_rett_identity(self):
        """Known gap: the Rett syndrome node of the MECP2 search carries OMIM:613454
        (FOXG1 congenital variant) as an xref, so FOXG1 patients are its patients and
        FOXG1 is not among its genes."""
        r = self.rows("eval/rett.gene.json")["MONDO:0010726"]
        self.assertEqual(r["gene_recall"], 1.0, r["diagnoses"])

    @unittest.expectedFailure
    def test_focus_is_one_disease(self):
        """Known gap: entities.py merges distinct diseases into one focus node (Lubs type
        in eval/rett.gene.json carries ~40 MONDO ids, Kanzaki in fabry.symptoms 2)."""
        for run in ("eval/rett.gene.json", "eval/fabry.symptoms.json"):
            for f, r in self.rows(run).items():
                self.assertLessEqual(len(r["mondo_ids"]), 1, (run, f, r["mondo_ids"][:5]))


if __name__ == "__main__":
    unittest.main()
