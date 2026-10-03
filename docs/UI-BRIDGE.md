# Body-map UI bridge

## Start

From the repository root, Python 3.11+; no additional packages required:

```sh
PYTHONPATH=src python3 -m garra.ui --port 8787
```

The local development server listens on `127.0.0.1:8787`. Default permitted UI origins are localhost and 127.0.0.1 on ports 5173 and 3000. For another development origin:

```sh
PYTHONPATH=src python3 -m garra.ui --allow-origin http://localhost:5174
```

This is a local development bridge, not a public production service. A deployed UI needs this service behind its application's backend/reverse proxy; localhost means the browser user's own machine. Add production authentication, resource limits, and appropriate deployment infrastructure before exposing it publicly.

## UI wiring

1. Fetch `GET /api/body-map` once. Its `regions` have stable IDs: `head`, `eyes`, `ears`, `chest`, `abdomen`, `arms`, `legs`, `whole_body`.
2. Map the UI's mesh/hotspot names to these IDs. A leg click should select region `legs`; it does not immediately submit a finding.
3. Filter `symptoms` by inclusion of the selected region in each symptom's `regions`. Show `label`, `description`, and optionally `hpo_label` and source.
4. Store selected symptoms in a set keyed by HPO ID so selecting the same symptom through two regions does not duplicate it. Preserve selections when changing regions; let the user remove them. Offer a whole-body list and accessible textual region controls.
5. Show the selected symptom list and a confirmation/search button. The UI should say this sends the selected HPO terms to Monarch for research matching. Do not submit names, dates of birth, files, or free-text patient details.
6. On explicit confirmation, call the POST below. Use loading, failure, empty-result, and populated-result states separately. Abort/discard outdated responses when the selection changes.
7. Render returned `cards` without calculating a second score in the UI.

A copyable TypeScript fetch client is in `examples/ui/atlas-client.ts`. It includes catalog retrieval, region filtering, typed cards, cancellation via AbortSignal, and search. Copy it into your existing UI project; no replacement body-map implementation is needed.

The starter menu has 11 HPO terms and eight region choices. IDs and official labels were checked against HPO release 2026-09-01. Region assignments and descriptions are application-authored and need clinical review. This is intentionally not a complete clinical symptom inventory. Unknown terms are rejected until the catalog is extended and checked.

## Search request

`POST /api/connections/search`, Content-Type `application/json`:

```json
{
  "hpo_ids": ["HP:0001324", "HP:0001252"],
  "confirmed": true,
  "metric": "jaccard_similarity",
  "min_score": 50,
  "limit": 5
}
```

These are public example terms (muscle weakness and hypotonia), not a patient case. They describe different findings; the UI must not select hypotonia automatically from weakness.

Accepted inputs: 1–20 menu IDs (deduplicated), limit 1–25. Unknown fields, unknown terms, coordinates, region-only requests, and unconfirmed selections receive 400. The default metric is Jaccard, with a provisional minimum of 50/100. For `ancestor_information_content` or `phenodigm_score`, omit `min_score` or use null. A numerical percentage filter with these native metrics is rejected.

## Response and rendering

A successful response has `status: ok` or `no_matches`, `cards`, `query`, `upstream`, `retrieval`, `omitted`, `notice`, and `message`.

Each card includes:

- `id`, `name`, `why_shown`.
- `similarity.display_label`, `similarity.value`, `similarity.metric`, `similarity.interpretation`. Label it **Similarity to your selected symptoms**. For Monarch's Jaccard metric, the displayed index is raw score ×100. It is ontology-based similarity, not a literal percentage of symptoms shared and not a diagnostic probability.
- `matched_phenotypes`: pairs of query term and disease annotation, their match score, shared ancestor, and whether the match is exact. Distinguish semantic matches from identical symptoms.
- `annotation_counts`: disease and query term counts. A high score against sparse annotations is explicitly flagged. The fewer-than-three warning is a UI heuristic, not a validated scientific cutoff.
- `claims`, `assets`, `papers`, `evidence`: the existing evidence-card structures. Render supporting and contradictory evidence, claim status, asset owner/access/reuse checks, and paper passage/context/access links. Show unknowns; do not generate missing content.
- `evidence_scope`: disease research, not a demonstrated mechanism in the user.
- `warnings`: render these near the score and evidence. Human Diseases includes more than rare genetic diseases; this first version does not silently filter or relabel that group as rare diseases.

The adapter asks Monarch for the top 50 results, then applies the display cutoff and limit locally. Empty results are not proof that no disease or connection exists. The results are not an exhaustive rare-disease search.

Cache metadata lives in `upstream.cache`; show retrieval time if `hit` is true. Responses are cached only in bounded process memory for 15 minutes (64 requests), with metric and query in the key. No stale fallback is silently substituted after expiry. The upstream source release is `unknown` because the search response does not supply it; retrieval time is recorded honestly.

The API is verified against `https://api.monarchinitiative.org/openapi.json`. The semantic search endpoint is `POST https://api.monarchinitiative.org/v3/api/semsim/search` with `termset`, `group: Human Diseases`, `metric`, `directionality: bidirectional`, and `limit`. The actual response is an array; `subject` is the matched disease, `similarity.subject_termset` contains disease annotations, and `similarity.object_termset` contains the query terms. Do not reverse this orientation.

Errors: 400 invalid input, 403 disallowed origin, 404 missing route, 413 oversized body, 415 wrong content type, 502 upstream/schema failure, 504 upstream timeout. Show an unavailable/retry state for upstream failures; never turn them into “no matches.”

## Data-team handoff: optional enrichment

Without enrichment, scores and matched phenotypes work live, but assets, papers, and reviewed biological claims remain empty. Supply verified disease research records using:

```sh
PYTHONPATH=src python3 -m garra.ui --enrichment data/research-catalog.json
```

Catalog structure:

- `schema_version`: 1.
- `diseases`: records with `id`, `name`, `scope: disease_research`, optional `coverage`, `claims`, and `assets` using the field contracts in `docs/CONNECTIONS.md`.
- `evidence` and `papers`: shared evidence/publication records using that same contract.
- `mappings`: optional map from a returned MONDO ID to the catalog's disease ID. Supply only reviewed exact equivalences. Raw cross-references are not automatically treated as equivalences.

Only matching disease records are attached. Keep all claim/asset IDs unique within each disease. Biological claims describe sourced research concerning the matched disease, not proof that a symptom-profile user shares its mechanism. Do not load an old pairwise connection packet unchanged; its anchor-specific relationships are not valid for an arbitrary symptom profile. Such catalogs need deliberate curation into the disease-research scope.

Asset existence, access, reuse conditions, publication metadata, and passages remain the fetch/curation team's responsibility. This bridge does not scrape papers or infer suitability from a similarity score.

## Tests

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_ui_bridge.py -v
```

The committed Monarch fixture is a trimmed real response to public example terms, retrieved during integration on 2026-10-03. Tests are offline, including HTTP tests with a local mock-backed server. They check query confirmation, IDs, native metrics, mapping, annotation warnings, response orientation, caching, schema drift, and CORS. No patient data is included.
