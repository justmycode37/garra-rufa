# Garra Rufa — Challenge Requirements

Source: Hack-Nation 7th Global AI Hackathon, **Challenge 05 — "AI Atlas for the World's Rare Diseases"** (OpenAI × Buffalo Initiative). Time budget: 24 hours.

## The problem in one line
Patient groups for untreated monogenic rare diseases can't find who shares their *mechanism*, what assets already exist, or what to do next — because knowledge is scattered and organized by disease *name* instead of mechanism + phenotype.

Context numbers: ~10,000 rare diseases · 80% genetic · ~5,000 monogenic · 350M people · <5% have an approved treatment. **10× goal:** help research advance toward a treatment 10× faster.

## What to build
A **knowledge graph** plus an **interface a family can understand**.

**Node types (required):** disease · gene · variant · mechanism · symptom/phenotype · patient group · paper · study · research asset (registry, natural-history study, model, biomarker, trial design). Investigators and funders are implied by the edge table.

**Every edge must carry:** relationship type, source citation, date, confidence, and whether it is *observed* or *inferred*, plus any contradicting evidence.

**Three questions the atlas must answer (Maria):**
1. Who shares our disease characteristics?
2. What useful work already exists?
3. What should we do together next? (an action that can be taken *this week*)

## Modules
### M1 — Graph Builder ("Connect the Evidence")
Start with **one disease cluster**, then show how it scales. Resolve names and synonyms to stable IDs (MONDO/OMIM/HGNC/HPO), and record source, date and confidence on each edge.

| Edge | Source |
|---|---|
| Gene–variant–mechanism | OMIM, ClinVar (variant *effect* matters, e.g. loss-of-function vs gain-of-function, not just a shared gene name) |
| Disease–phenotype | HPO (separate broad symptoms from informative ones, e.g. weight by IC/IDF) |
| Publication–claim–investigator | PubMed / PMC |
| Study–condition–intervention | ClinicalTrials.gov |
| Funding–researcher–asset | NIH RePORTER |
| Patient org–disease–registry | NORD, Global Genes, Orphanet, EURORDIS, verified group sites |

### M2 — Trust ("Make Each Connection Trustworthy")
- Show the supporting source next to each link, and distinguish observations from inferred links.
- Surface contradictory findings.
- **No supported route? Say so.** Explain what the search covered, what evidence is missing, and what to test next. This is a first-class outcome, not an error state.

### M3 — Action ("Turn the Network into Action")
1. **Mechanistic overlap:** cluster by variant effect + pathway + phenotype, not by name or category. Same gene with different mechanisms should land in separate clusters; different genes with the same mechanism should land in the same cluster.
2. **What can be shared:** map registries, natural-history studies, models, biomarkers and trials onto clusters. Flag duplicate efforts and judge whether an asset or study design is adaptable.
3. **Network overlap:** detect when "unrelated" communities share a key opinion leader (researcher, clinician, biotech rep, investor or funder).

## UX principles (explicit in brief)
- **Low ink, high signal:** whitespace does the work, and color always carries meaning.
- **One global search:** a disease, gene, symptom, patient group or mechanism all open the same graph, with visible synonym resolution.
- **Progressive reveal:** summary first, depth on click.
- **Explain every edge:** show source, type, confidence and contradictions beside each edge.
- **Patient action view:** shared assets, clinical pathways, partners and next experiments, with viable leads clearly separated from unsupported links.

Illustrative concept in the brief: mechanism-cluster legend on the left, graph in the center (node size = centrality, dashed lines = cross-cluster bridges), selected-disease panel on the right (investigators, bridges, patient groups, "Explore connections →"), and a "Viewing as: Maria" persona switch.

## Personas
| Persona | Needs | Leans on |
|---|---|---|
| **Maria**, patient-org leader (primary) | Closest clusters, reusable assets, groups on same pathway, shared clinical opportunity plus the next validating experiment. *Optional:* investors in similar modalities, funder RFAs | Clustering · Pathway navigator · Connector |
| **Devon**, newly diagnosed caregiver | Plain language at 2 a.m.: their exact patient group, or the closest related ones and how to start the missing one. Honest "nothing known yet" | Search |
| **Priya**, biotech scout | Input: one therapeutic mechanism (e.g. protein replacement, silencing). Output: ranked clusters with evidence, patient groups, infrastructure, named contacts | Clustering · Search |
| **Dr. Osei**, academic | Who else works on my mechanism under any gene name, and how to reach them | Connector |

## OpenAI usage (needed for track prizes)
- **Extract:** pull genes, variants, phenotypes, claims and investigators out of abstracts into edges tied to their source.
- **Reconcile:** resolve synonyms so each entity maps to one stable node.
- **Explain:** turn a graph path into plain language, with each step citing its edge.

## Stretch goals
- Invent new uses: propose an experiment that tests a shared mechanism, surface an overlooked partner, or let patient groups contribute missing evidence.
- Reduce the burden of care while treatment research is ongoing.
- Extra sources: Jackson Lab (models), RareConnect, bioRxiv/medRxiv.

## 10× moonshot (must be argued)
Pick one milestone, such as launching a shared natural-history study or reaching a go/no-go on a therapeutic candidate. Compare the **status-quo timeline vs. our route** and state the assumptions behind 10×.

## "That is the bar" — the demo journey
Maria types her disease → disrupted pathway → another gene → related disease → that disease's patient group (every edge sourced) → existing registry + study design (what's reusable, what differs, what needs expert review on biology/eligibility) → a **sourced partnership proposal**. Alternative ending: no supported lead, a clear account of what's unknown, and the next question to test.

## Judging criteria
1. **Graph quality:** node/edge design, defensible clustering, useful paths, counterexamples, uncertainty.
2. **Evidence integrity:** sourced, cross-checked; data vs. hypothesis vs. clinical proof kept distinct.
3. **Patient progress:** isolated diagnosis → justified collaboration + reusable asset + next milestone.
4. **10× impact:** milestone, timeline comparison, assumptions.
5. **Ambition & product craft:** intuitive; enables collaboration at a scale no single community could reach.

## Deliverables
- [ ] Working prototype, deployed or easy to run locally, that judges can search live
- [ ] Repo with a README covering **architecture and how to reproduce the dataset**
- [ ] Team video
- [ ] 1-minute walkthrough following one family or group to a justified collaboration, or to an honest gap plus an investigation plan

## Gap analysis vs. current codebase (as of 2026-10-03)
Already have: Next.js app, OpenAI Responses API integration (`src/lib/ai.ts`), PubMed literature fetch, auth, workspace records, community, 3D body-region view (`BodyGraph.tsx`), 8 curated diseases (`src/lib/knowledge.ts`).

Missing or misaligned:
- **Organizing axis:** the app is organized by **body region and disease name**, while the brief explicitly calls for organizing by **mechanism + phenotype**. The body map could stay as an entry point for Devon, but clusters need to be mechanism-based.
- **No typed graph:** `Disease` is a flat record. There are no variant, mechanism, phenotype (HPO), paper, study, asset or investigator nodes, and no edges carrying source, date, confidence or observed-vs-inferred.
- **No stable IDs:** MONDO, OMIM, HGNC and HPO IDs are missing, and so is synonym resolution.
- **No clustering, bridges, or shared-KOL detection.**
- **Thin assets and orgs:** each disease has one org. Registries, natural-history studies, trials (ClinicalTrials.gov), models and NIH RePORTER grants are missing.
- **Dataset fit:** most current diseases (SMA, Pompe, Fabry, Marfan, etc.) already have approved treatments or large communities. The brief targets *untreated* monogenic diseases, so we need one focused cluster with real cross-gene mechanism sharing.
- **Missing UI:** "no supported route" honest-gap UI, persona views (Maria/Devon/Priya/Osei), the 10× timeline narrative, and the README dataset-reproduction section.
