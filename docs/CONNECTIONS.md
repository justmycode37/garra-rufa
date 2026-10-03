# Evidence-linked disease connections

This layer consumes a normalized JSON packet supplied by the data-fetch team. It makes no network calls and does not infer evidence from papers or fetch Monarch data itself. The existing journey and similarity commands remain available; this command is the separate, evidence-linked UI path.

## Run the prototype

Synthetic fixture:

```sh
PYTHONPATH=src python3 -m garra.atlas connections examples/connections-demo.json --min-score 50 --limit 5 --html connections.html
```

Live anchor (Monarch + Orphadata + Open Targets, disk cache per MONDO id):

```sh
PYTHONPATH=src python3 -m garra.atlas packet "Pompe disease" --candidate-limit 5
PYTHONPATH=src python3 -m garra.atlas pipeline "Pompe disease" --offline --no-openai --html connections.html
```

Cached steps and `packet.json` land in `data/cache/connections/<MONDO_id>/` (gitignored). Re-run with `--no-cache` on `packet` or `pipeline` to refresh upstream data.

Batch prefetch for a curated list (default `examples/anchors.txt`):

```sh
PYTHONPATH=src python3 -m garra.atlas prefetch-packets --sleep 1.2
PYTHONPATH=src python3 -m garra.atlas prefetch-packets --raresource-jsonl data/raw/raresource/diseases.jsonl --limit 30
```

Summary: `data/cache/connections/prefetch_report.json`. Per-anchor log: `prefetch_index.jsonl`. Skips anchors that already have `packet.json` unless `--refetch`.

Open `connections.html` in a browser. The example is explicitly synthetic and uses example.org links, not real disease findings. Replace the packet with verified data before a real demonstration. JSON cards are also printed to stdout.

Python entry point for the frontend/backend:

```python
from garra import build_connections, render_connections
result = build_connections(packet, min_score=50, limit=5)
html = render_connections(result)
```

Call `render_connections` only with the validated output of `build_connections`. External text is HTML-escaped; input links are limited to HTTP(S).

## Fetch-team handoff: packet version 1

Use `examples/connections-demo.json` as the executable contract example. Top-level fields:

- `schema_version`: 1.
- `demo`: true only for synthetic packets. Production integrations must not mix synthetic records into a real packet.
- `anchor`: canonical `id`, `name`, optional `equivalent_ids` containing only reviewed exact equivalents.
- `candidates`: distinct diseases with `id`, `name`, `similarity`, `coverage`, `claims`, and `assets`.
- `evidence`: source records indexed by unique `id`.
- `papers`: publications indexed by unique `id`.

### Similarity from Monarch

The fetch team should call Monarch's documented semantic similarity search with a disease's phenotype profile and Human Diseases as the target group. Normalize each response into:

```json
{
  "provider": "Monarch Initiative",
  "metric": "jaccard_similarity",
  "value": 0.68,
  "release": "ACTUAL_SOURCE_RELEASE",
  "retrieved_at": "ACTUAL_RETRIEVAL_TIME",
  "source_url": "https://api.monarchinitiative.org/v3/docs"
}
```

The numerical example above is illustrative, not a fetched result. Preserve the actual metric, raw value, source release, and retrieval time. If the release is not supplied by the upstream service, use an explicit unknown marker and the actual retrieval time; do not invent a version. Keep one provider, metric, and release per packet. The fetch adapter is responsible for matching the current API response schema and for preserving query term IDs and request provenance as additional fields if available.

Only `jaccard_similarity` is converted from its fixed 0–1 range into an index out of 100. The configurable default 50/100 cutoff is a provisional discovery filter, not a validated clinical threshold. `ancestor_information_content` and other native metrics are displayed in native units and ranked without applying the 0–100 filter; the UI explicitly says so. Never normalize by the maximum returned result or assume a native score is a percentage. Unknown/absent scores cannot become zero; omit unscored candidates from the ranked packet and report the fetch gap upstream.

Cards sort descending by raw score, with disease ID breaking ties. Exact anchor/equivalent entries are excluded. The output explains threshold and top-k exclusions. If all candidates are excluded, an honest no-results state is returned.

### Biological claims

Each claim has a unique candidate-local `id`, a readable `statement`, and:

- `relationship`: `shared_gene`, `shared_pathway`, `shared_phenotype`, or `shared_molecular_effect`.
- `assessment`: `supported`, `hypothesis`, or `contradicted`.
- `reviewed`: boolean; only a genuinely reviewed, supported, sourced claim becomes supported in the UI.
- `supporting_evidence_ids`, `contradicting_evidence_ids`: references to evidence records.
- `context`, `limitations`: source-grounded effect, variant, model, population, or transfer limitations where available.

Contradictory evidence produces an explicit conflict status. A shared gene cannot establish a supported biological connection by itself. A supported pathway/phenotype relationship does not establish compatible molecular effects. This layer validates linkage and labels; it cannot independently verify the scientific correctness of upstream claims or whether a human review occurred.

`coverage` maps modalities to `available`, `missing`, or `unknown`. It is displayed separately from similarity rather than replacing missing data with zero. The fetch team must describe both diseases' relevant coverage accurately.

### Evidence and papers

Evidence fields: `id`, `source`, `url`, `retrieved_at`, `locator`, optional short `passage`, and `paper_ids`. A locator identifies the source record, section, figure, or paragraph. Passages should be concise permitted excerpts and tied to the exact claim; do not copy whole copyrighted articles. Preserve supporting and opposing records separately.

Paper fields: `id`, `title`, `url` (abstract/publisher), `access` (`open_access`, `restricted`, `unknown`), optional verified `full_text_url`, `study_type`, `population`, and `limitations`. A full-text URL is accepted only when access is explicitly open access. Missing context is shown as unknown, not fabricated. Only papers linked through a displayed claim or asset are shown; unrelated search results are excluded.

### Assets

Required fields: `id`, `name`, `kind`, `owner`, `url`, `access_conditions`, `claim_ids`, `evidence_ids`, and nonempty `reuse_checks`. Use an explicit unknown description when ownership or access has not been confirmed. Assets must cite evidence and a biological claim in the same disease card. Suggested kinds include registry, experimental model, assay, dataset, biomarker, and study protocol.

Each asset remains potentially reusable and requires assessment. Its reuse checks should be concrete: mechanism/model suitability, endpoint applicability, population differences, access authorization, license, or protocol adaptation. A connection does not establish trial eligibility or transfer of therapeutic response.

## Checks

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_connections.py -v
```

These deterministic tests require no raw downloads. They cover score bounds/thresholds, native metrics, comparability, identity exclusion, missing evidence, conflicting claims, paper linkage, asset checks, input immutability, ranking, and HTML safety.

API reference: https://api.monarchinitiative.org/v3/docs (semsim). Integration with live responses remains the fetch team's boundary; this implementation has not verified a live Monarch response.
