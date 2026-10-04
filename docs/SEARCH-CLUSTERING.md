# Unified search and explainable clustering

## Start the working explorer

```sh
PYTHONPATH=src python3 -m garra.ui \
  --atlas data/atlas.sqlite \
  --bridge data/derived/discovery/bridge.json \
  --port 8788
```

Open http://127.0.0.1:8788/explore. This is a working local UI served by the existing backend, not a change to the Vercel deployment. The atlas and validation snapshot are local ignored data; paths are examples, not bundled datasets. Without inputs the API returns honest empty results.

Build the real multi-disease snapshot from the collected cache:

```sh
PYTHONPATH=src python3 -m garra.discovery.build_snapshot --verify-genes \
  --evidence runs/live-validation/evidence.kg.json
```

The builder reads saved Monarch association records and Open Targets pathways, verifies exact Ensembl-to-HGNC mappings, and writes source/bridge/clusters/manifest JSON under `data/derived/discovery/`. Gene mappings are cached there; omit `--verify-genes` for an offline rebuild after verification. Without verified mappings, pathway edges are skipped explicitly. Original cache files and the atlas remain unchanged.

The first real build contains 308 diseases, 366 candidate pairs, 178 overlapping pathway groups and 2 phenotype clusters. All 138 HGNC lookups succeeded. Six source responses were truncated; their paths and counts appear in the manifest. These numbers describe this collected subset, not the entire rare-disease catalog. Many pathway groups are broad or overlap; group counts are not counts of confirmed shared mechanisms.

Association subject/object IDs are preserved, even when the query returned a subtype different from its requested parent. Negated phenotype associations are excluded. Every source edge carries input-file provenance and timestamps; manifests include input hashes. Eight existing unreviewed paper claims remain available only in their applicable context. No fake research clusters are inserted to populate the UI.

## Search API

`POST /api/search`, JSON:

```json
{"mode":"text","query":"Pompe","kind":"disease","limit":20,"offset":0}
```

`kind` is optional. Search covers disease, gene, phenotype, pathway, process and anatomy nodes in the bridge, plus disease/gene aliases and phenotype/pathway labels in the optional atlas. Exact IDs rank before exact labels/aliases, prefixes, and substrings. Ties use stable IDs. Pagination returns `total`, `offset` and `limit`. Multiple exact matches are flagged; the caller must select an entity, not silently treat matching names as identity equivalence. Imported alias quality remains dependent on the source atlas.

Results carry `cluster_ids` and `bridge_available`. A searchable atlas entry can lack bridge evidence. Atlas names do not create biological connections or silently merge different identifiers. Gene results are searchable nodes; gene-only overlap does not generate disease clusters.

Confirmed symptom search uses the same endpoint:

```json
{"mode":"phenotype","hpo_ids":["HP:0001324"],"confirmed":true,"limit":5}
```

It delegates to the existing validated Monarch service and preserves its native score metadata, error handling and enrichment. Results remain symptom-to-disease cards, with `cluster_ids` attached where exact identifiers match. It supports the existing limited symptom menu; free text is not converted into patient findings. The existing `/api/connections/search` remains compatible.

## Cluster API

- `GET /api/clusters`: group summaries.
- `GET /api/clusters?kind=pathway&entity_id=MONDO%3A0009290`: filter by member or process ID. Kind can be `pathway` or `phenotype`.
- `GET /api/clusters/{id}`: members, full pair assessments, source paths, supporting/contradicting claims, and referenced publications.

Groups are generated once on startup from a version-1 `atlas bridge` output. Their IDs hash type/process/membership and remain stable for the same membership. The `snapshot` hash versions the bridge and threshold; it does not version the optional atlas search database.

### Pathway groups

Overlapping groups collect diseases sharing a process in bridge candidate connections. One disease may occur in many groups. A group means a shared research topic, not equivalent disruption of that process. Opposing effects, missing context and unreviewed evidence remain on the original pairs. This does not claim to be learned community detection or a validated mechanism cluster.

### Phenotype clusters

Deterministic complete-link agglomeration processes eligible pair scores from highest to lowest, with stable identifier tie-breaking. Two groups merge only when **every cross-pair** is present and meets the configured exact-HPO Jaccard threshold. This prevents A–B–C chains from inventing A–C similarity. Phenotype clusters form a partition among eligible members; pathway groups overlap independently.

`--cluster-threshold` defaults to 0.5 and must be in (0,1]. This is a provisional discovery setting, not a clinically calibrated cutoff. Missing similarities remain unknown. Only `exact_hpo_jaccard` scores are eligible; other metrics cannot silently use the same threshold. Singleton diseases are searchable but are not shown as clusters.

**Input coverage matters:** build the bridge with a phenotype threshold no higher than the clustering threshold, or relevant pairs may already have been discarded. Lowering the cluster threshold cannot recover discarded input. Build a new bridge snapshot to change coverage. Broad pathways can form large groups; inspect member counts and source specificity before interpreting them.

## Reproducibility and limits

Search and grouping are offline over loaded data. Confirmed symptom mode alone calls the existing Monarch adapter. No model runs during text search or clustering. No cluster promotes an extracted claim to reviewed status, creates a community membership, or implies asset reuse is safe.

The current engine holds the index in memory and scans labels for substring search. Complete-link clustering and pair-detail payloads can grow quadratically for dense graphs. It is intended for curated graph snapshots; large catalog-wide graphs need measured performance and a database-backed search/paginated pair service before production scale claims. JSON snapshots are trusted local build artifacts, validated for IDs, scores, duplicate pairs and evidence references at startup; they are not accepted as public API uploads.

Anatomy nodes are searchable when supplied. Connecting clusters to body regions still requires the separately planned sourced anatomy-to-UI mapping; grouping does not infer body-region associations.

## Frontend integration and validation

The local explorer supports entity search, pagination, confirmed-symptom selection, group filtering, member inspection and paper links, and renders text using DOM textContent. The same endpoints can be consumed by the separately hosted frontend via its backend/proxy. Add only its exact origin using `--allow-origin` when needed. The development server binds to loopback; it is not a production deployment server.

Regression tests cover overlapping process groups, complete-link behavior, missing data, deterministic membership, threshold changes, exact/alias search, disambiguation, pagination, reference integrity, HTTP errors/origin checks and search → cluster → evidence retrieval. Synthetic tests demonstrate multi-disease behavior; they do not validate real biological connections.
