# Source graph to paper graph: disease-pair evidence bridge

The source graph from `src/query-test/main.py` supplies biomedical associations. The paper graph from `src/query-test/evidence/main.py` supplies extracted statements. `garra.bridge` joins these without treating overlap or publication counts as proof of compatible mechanisms.

## Run

Install optional research dependencies with `python -m pip install '.[research]'` to run the research pipeline and its tests. The bridge itself uses the standard library.

After collecting literature, the existing evidence extraction command now includes `disease_bridge` in its output:

    python src/query-test/evidence/main.py runs/example.papers.json -o runs/example.kg.json

This command uses the configured OpenRouter model and may make paid model calls. This implementation was tested offline with controlled extraction responses, not by running a paid extraction job.

For existing source/evidence graph JSON:

    PYTHONPATH=src python -m garra.atlas bridge runs/example.json runs/example.kg.json --out runs/example.bridge.json

Optional `--mappings mappings.json` accepts a JSON list of explicit exact mappings:

    [{"from":"MONDO:0000001","to":"ORPHA:1","relation":"same_entity","reviewed":true,"source_url":"https://example.org/mapping"}]

These identifiers and URL are illustrative, not a verified biological mapping. A producer is responsible for checking mapping evidence; the bridge validates the declaration and rejects known subtype/related conflicts. Do not mark a mapping reviewed just to make a join succeed. Mere xrefs and equal names do not merge nodes. Fuzzy, text and PubTator-only normalized identities remain unresolved for cross-graph joins. Known identifier namespaces and types are checked; identifier syntax alone does not verify scientific identity against an external authority.

## Output

- `nodes`, `identity_links`, `source_relations`: entity joins and original relationships. Subtype and related edges remain relationships rather than equivalences.
- `papers`: PMID/DOI/PMCID-deduplicated publications with the input records preserved. Conflicting PMIDs sharing an identifier cause an explicit error, not silent merging.
- `claims`: subject, relationship, object, effect direction, polarity, organism/tissue/model/population/variant, study type, limitations, paper ID, quote and passage/section locator. Every automated claim is unreviewed.
- `candidates`: disease pairs, candidate reasons, exact-HPO Jaccard (or null when coverage is missing), shared process IDs, source paths and claim references.
- `assessment`: a separate unreviewed pair inference. Same-direction reports yield a hypothesis; opposing or negated reports yield potential conflict; insufficient directional evidence remains insufficient. Context differences and unknowns remain visible. No clinical probabilities or automatic supported labels are produced.
- `issues`: unresolved identities, invalid mappings, missing publications and legacy quote provenance.

## Candidate discovery

Phenotype overlap above `--phenotype-threshold` (default 0.5; provisional discovery setting) is one route. Shared pathways/processes, including direct disease effects extracted from papers and pathways of associated genes, are another independent route. No phenotype cutoff is applied to that second route. Shared genes alone and co-mentions create no pair candidate. Pathway membership creates a reason to investigate, not evidence of a disease-associated molecular effect. The current effect comparison requires explicit disease-to-process claims on both sides; gene-only effects need a reviewed disease/variant interpretation and are not silently transferred.

Scores use the available exact HPO annotations, not ontology-aware semantic similarity, and are distinct from the existing Monarch score. Missing profiles return null. No composite confidence percentage is invented.

## Extraction and migration

The existing LLM extraction prompt now asks for direction, polarity and experimental context independently of therapeutic benefit/harm. Unknown values remain unknown. Exact quoted text (allowing whitespace normalization only) must occur in the stated passage. Quote presence does not verify entailment or study quality. Model output cannot set a claim to reviewed. Results still require a person to check meaning, context and limitations before scientific use.

Older paper graphs lack exact-passage verification metadata: the bridge reports and excludes those claims. Rerun extraction against available source text to verify them; do not manually add verification flags. Existing raw/model-response caches are preserved. Prompt changes change the model cache key.

Source graph generation no longer merges raw xrefs or matching gene symbols automatically. The evidence graph no longer merges entities by label. Rebuild older source graphs if they previously collapsed a disease with its subtype: the bridge cannot reconstruct distinctions already erased upstream.

The new output is a backend JSON contract. It does not yet wire disease-pair assessments into the body-map UI, establish reusable-asset suitability, or replace the legacy solution-ranking heuristic. The new bridge deliberately does not reuse that heuristic's numerical confidence. No new bulk literature collection was run for this change.
