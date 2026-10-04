# garra-rufa

Rare-disease research atlas for Hack Nation.

Approved downloaders for the rare-disease atlas. Every fetch starts from a row in `src/garra/sources/catalog.py`. A file is kept only when the host is allowlisted, the body is larger than the empty-stub floor, and the expected header is present. The saved `manifest.json` records the publisher, license, canonical URL, resolved URL, time, byte size, and sha256.

## Connected web app

The Next.js frontend and AI workspace live in [`webapp/`](webapp/README.md).
Public website: **https://garra-rufa.vercel.app**. Vercel hosts the web app and
protected research service; Supabase stores hosted workspace data. Supabase Auth supports email/password and Google sign-in; AI uses the app’s
server-side OpenAI API key. See [Google setup](webapp/docs/GOOGLE-SIGN-IN.md).
To run the full product locally:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[research]'
cd webapp
npm ci
npm run dev:all
```

Open **http://127.0.0.1:3000**, choose **My space**, select your role, then create an
account or sign in on the next step. The app calls this repository's graph adapters and separate
literature pipeline, then displays retrieved records in inline frosted cards.
The Python service binds to 127.0.0.1:8787. It exposes read-only
`POST /api/research/search` and `POST /api/research/papers` endpoints with a short
public `query`, optional `limit` (1–20), and `category` (`all` or `contacts`).
Downloaded indexes are used when present; otherwise supported public sources are
queried live. Bulk datasets, accounts, credentials, and local caches stay out of Git.

See [the testing guide](docs/WEBAPP-TESTING.md) for checks and hosting limitations.

## Fetch the default slice

```bash
PYTHONPATH=src python -m garra.sources --list
PYTHONPATH=src python -m garra.sources
```

Files land in `data/raw/<source-id>/`. That directory is gitignored.

One source:

```bash
PYTHONPATH=src python -m garra.sources --only hpo_genes_to_disease --only orphadata_genes
```

ClinVar `variant_summary.txt.gz` is about 450 MB and stays off unless you pass `--allow-large`.

## Query one disease

```python
from garra.sources.queries import search_clinicaltrials, search_pubmed, search_reporter

search_clinicaltrials("Pompe disease")
search_reporter("Pompe disease")
search_pubmed("Pompe disease")
```

Set `NCBI_EMAIL` before calling PubMed. Patient-organization directories (NORD, Global Genes, EURORDIS, Orphanet patient orgs) are listed as manual sources and are not downloaded.

## Local data

Most API lookups (ontologies, Orphadata, HPO, Monarch, Open Targets, ClinVar, GTEx, HPA,
MeSH, ClinicalTrials.gov, RePORTER, a rare-disease PubMed subset, PubTator, LitVar) can be
answered from local indexes in `data/local/` instead of the network:

```bash
PYTHONPATH=src python -m garra.local download
PYTHONPATH=src python -m garra.local build
```

Without the indexes everything falls back to the live APIs. See [docs/LOCAL_DATA.md](docs/LOCAL_DATA.md).

## Backend (graph + journey, no UI)

See [docs/BACKEND.md](docs/BACKEND.md). After raw files are fetched:

```bash
PYTHONPATH=src python3 -m garra.atlas build
PYTHONPATH=src python3 -m garra.atlas journey "Pompe disease"
```

For Python integrations, import `resolve_query` from `garra.graph.resolve`, `similar_diseases` from `garra.similarity`, and `build_journey` from `garra.actions.journey`.

## Body-map UI bridge

```bash
PYTHONPATH=src python3 -m garra.ui --port 8787
```

Keep the terminal running. Open http://127.0.0.1:8787/ to see the routes, or `/api/body-map` for the symptom-menu JSON. This is the API backend, not a visual body-map frontend. See [the UI integration guide](docs/UI-BRIDGE.md) and [TypeScript client](examples/ui/atlas-client.ts).

The live Monarch adapter returns phenotype similarity and matched terms. Verified biological claims, papers, and assets require a separate curated catalog. The 50/100 discovery filter is provisional, not a clinical threshold. The server binds locally and is intended for development.

## Evidence-card example

```bash
PYTHONPATH=src python3 -m garra.atlas connections examples/connections-demo.json --html connections.html
```

This example is synthetic and explicitly labeled. See [the evidence contract](docs/CONNECTIONS.md).

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Tests use fixtures and mocked upstream calls; no downloaded datasets or live API are required.

### Community discovery API

The UI bridge includes separate **disease communities**, **mechanism groups**, and
**collaboration projects** at `GET /api/communities`. It supports exact disease/process
ID filters, public detail pages, and project-to-community links. The default catalog
is empty; use `--communities examples/communities.demo.json` for explicitly fictional
UI demo records. Joining and posting are not enabled. See [Community integration](docs/COMMUNITY.md)
for the schema, endpoints, and frontend integration steps.

### Unified search and clustering

`POST /api/search` supports entity text search and confirmed phenotype search.
`GET /api/clusters` exposes overlapping pathway groups and complete-link phenotype
clusters with evidence details. Run the UI bridge with `--atlas` and `--bridge` to
load your data, then open `/explore` for the working local search/group browser.
See [Search and clustering](docs/SEARCH-CLUSTERING.md) for commands, API contracts,
coverage requirements and deployment limitations.
