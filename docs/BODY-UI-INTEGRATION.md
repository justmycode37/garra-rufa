# First anatomical UI/backend integration

Run from `webapp/` with Node 22.13 or newer:

```sh
npm ci
npm run dev:all
```

Open http://127.0.0.1:3000/?view=atlas, or choose **Atlas** from the main navigation. This is a public view within the main app, with the existing brand, Home, About, account and My space controls. It reuses the same `DiseaseAtlas` as the workspace. The old `/discovery` URL redirects to this view. It does not create an account, access patient records, or bypass private workspace authorization.

The launcher starts Python and Next.js together. `GARRA_RESEARCH_PORT` changes the default 8787 backend port. `GARRA_RESEARCH_URL` is passed server-side to Next.js; browsers call same-origin Next routes. Polling avoids excessive filesystem watchers in repositories containing large datasets.

## Local data required

- `data/atlas.sqlite`: entity/alias search.
- `data/derived/discovery/bridge.json`: real 308-disease graph, 366 candidate pairs, 180 groups.
- `data/ontology/hp.obo`: HPO ontology. This integration uses the previously downloaded local copy; each regional response exposes its SHA256. Clinical review of the UI mapping remains pending.

Build the graph with `python3 -m garra.discovery.build_snapshot` as described in SEARCH-CLUSTERING.md. Data files remain ignored and must be provisioned separately on another machine. The startup script reports missing files rather than silently displaying demo clusters. `GARRA_BRIDGE` and `GARRA_HPO_ONTOLOGY` override those two files.

## What is connected

The single shared `webapp/src/lib/body-regions.json` defines 40 UI regions. Python loads the same file and validates every HPO root. A disease is included only when a source-graph phenotype annotation matches the root or an ontology descendant. The source disease identifier is preserved, including subtypes. Clicking a region is a discovery filter, never a confirmed patient symptom.

The iris mapping now uses HP:0000525, rather than the whole-eye root. Pituitary uses HP:0012503. Broad hand-bone, leg-bone and airway mappings explicitly disclose their broader scope. Other labels are display categories, not diagnostic or anatomical assertions.

The existing 3D graph consumes `/api/atlas`, proxied to the regional snapshot. Disease expansion loads recorded phenotype/gene/pathway edges through `/api/entity` before the pre-existing live-research fallback for entities outside the snapshot.

The new Connected research panel supports:

- Regional disease annotations, with the exact HPO features behind each inclusion.
- Unified entity search, with ambiguous names kept separate.
- Pathway and phenotype groups, filtered by regional membership.
- Pair similarity, source paths, evidence status, claims, publication locations, limitations, and paper links.
- Navigation between body regions spanned by candidate disease pairs.

This is an anatomical overlay on existing groups, not a new assertion that organs interact. A disease can occur in multiple regions; counts are not independent. The panel explicitly distinguishes a globally searched disease from the current anatomical selection. A zero count indicates missing coverage in this snapshot.

## API contracts

Python:
- `GET /api/regions`: IDs, HPO roots, labels, counts and ontology hash.
- `GET /api/regions/{id}`: diseases/annotations, region-filtered groups and other regions spanned by candidate pairs.
- `GET /api/entity?id=MONDO:...`: recorded source graph around an entity and gene-to-pathway edges.
- Existing `/api/search`, `/api/clusters`, `/api/clusters/{id}` remain available.

Next.js: `/api/discovery?resource=regions|region|clusters|cluster|search`, with `id` for region/cluster details, `entity_id` for group filters, and POST only for search. Fixed upstream routes prevent user-selected destination URLs. Existing chat `/api/search` is unchanged.

## Tested scope

Browser checks: 3D heart selection loads 65 disease records; iris selection loads 19; unified Pompe search returns identifier choices; selecting Pompe opens its groups; glycogen hydrolysis displays actual members and an unreviewed passage with study context. Keyboard and mouse activation load graph relationships. Pointer capture now keeps a floating node clickable while its position changes; the canvas ignores releases without a corresponding canvas press. Browser verification traversed Heart → Myhre syndrome → SMAD4 → pathway nodes using mouse clicks. Ordinary panel buttons, region controls and search also worked.

Automated coverage includes HPO descendant mapping, specific region roots, candidate region links, graph expansion integrity, proxy route validation and backend errors. No production deployment or patient workflow was performed. Source truncation and limited paper evidence remain documented in the dataset manifest.


## Hosted deployment

The root Python function now supports the same discovery routes, regional atlas, and source graph expansion. All research/discovery requests require the existing shared `GARRA_RESEARCH_TOKEN`; only `/health` is public. Unknown routes return 404. `/health` returns 503 if the discovery bundle cannot load.

`deploy/discovery/` contains a compressed public-source snapshot, ontology, entity/alias index, and body-region mapping, with per-file SHA256 checksums and sizes. It is intentionally versioned outside the ignored local `data/` directory. It does not include patient workspaces, credentials, raw downloads, or research caches. Candidate claims remain unreviewed. The build verifies and expands the bundle to `data/hosted/`, included in the Python function. Original local datasets remain unchanged.

After updating source data or the body mapping, regenerate the bundle from the repository root:

```sh
python3 scripts/export_hosted_discovery.py
python3 scripts/prepare_hosted.py
```

Deploy the root backend and `webapp/` frontend as the two existing Vercel projects. Preserve their shared private token and set frontend `GARRA_RESEARCH_URL` to the backend URL. The frontend reads this configuration server-side only. Vercel sign-in/project access is required; no production deployment was performed during this change.

Validation: 192 Python tests, 147 frontend tests, TypeScript checking, production build, repository lint, and local HTTP tests of the actual hosted handler with the packaged snapshot. The production-built Next UI was also run against that handler: regions, Heart details, clusters, unified search, anatomical graph and disease expansion all returned 200 through the frontend proxy; mouse selection opened Myhre syndrome and loaded connected research. The HTTP check covered health, all 40 regions, Heart, unified search, disease expansion, anatomical graph, 180 clusters and cluster details. Live Vercel routing, external paper providers and authenticated patient workflows still require separate production verification.


## Publication claims in the anatomical graph

Regional pages now include available gene/process connections and publication claims for the loaded diseases. Entity expansion preserves the same evidence. Each paper connects to explicit claim nodes, which connect separately to the subject and object. A gene-level claim is not converted into a direct disease-mechanism assertion, and a shared phenotype alone cannot attach an unrelated paper.

Claims have their own filter and show passage, direction, polarity, review status, study type, context, location and limitations. Paper and claim cards link to PMC full text when an identifier exists, otherwise to the PubMed record when available. Pathways/processes are separately counted from reusable assets. Counts include the selected record and refer only to the loaded connected graph. Zero does not mean that no publications or resources exist elsewhere.

The current Pompe example (MONDO:0009290) contains one publication and eight unreviewed claims. This change connects existing evidence; it does not fetch additional literature, trials, specialist contacts or reusable assets. Other diseases may still have no linked publication claims.
