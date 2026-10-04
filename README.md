# garra-rufa

Rare-disease research atlas. A Python backend collects public biomedical data, joins it into
graphs, and serves it over a local HTTP API. A Next.js app in [`webapp/`](webapp/README.md)
turns that API into a 3D body atlas, graph viewers, and an AI workspace.
Hosted at **https://garra-rufa.vercel.app**.

## How data flows

```
public sources ──► local indexes ──► pipelines ──► snapshots ──► Python API ──► Next.js ──► user
 (bulk + live)     data/local/*.sqlite  query-test     bridge.json    garra.ui      /api/*
                   data/atlas.sqlite    discovery      graph builds   :8787         :3000
```

### 1. Accumulate

Three ways data gets in. All of it lands under `data/`, which is gitignored.

- **Bulk downloads into local indexes** (`garra.local`). `download` fetches about 24 GB of
  release files (Mondo/HPO/Uberon/GO ontologies, Orphadata XML, the Monarch KG, Open Targets
  parquet, ClinVar, GTEx, HPA, MeSH, the ClinicalTrials.gov export, PubTator). `build`
  converts each into a SQLite index in `data/local/`. PubMed and NIH RePORTER come from
  separate downloaders in `src/data/`. PubMed is cut down to a rare-disease subset.
- **Curated raw files** (`garra.sources`). Every file starts from a row in
  `src/garra/sources/catalog.py`. A download is kept only if its host is allowlisted, its
  size is above a floor, and its header matches. Each one is recorded in `manifest.json`.
  `garra.atlas build` loads these into `data/atlas.sqlite`, the disease, gene, and alias index.
- **Live APIs as fallback.** `garra.local.router` has a handler for each upstream API that
  returns responses in that API's own shape. Fetch code asks the router first and only
  falls back to the network if no index can answer. So every pipeline runs the same with or
  without local data, just slower without it. Set `GARRA_LOCAL=0` to force live calls.

### 2. Funnel

- **Query expansion** (`src/query-test/main.py`). Turns one term (disease, CURIE, gene, or a
  symptom list) into a graph. Each iteration sends the frontier through every source in
  `src/query-test/sources/` that accepts it. Entities are deduplicated by shared IDs and
  xrefs. Symptom and gene inputs first rank candidate diseases by HPO coverage. Group
  sources (Orphanet, ERN, EURORDIS, NORD, RDCRN, trials) attach organisations, centres, and
  registries.
- **Literature** (`src/query-test/literature/`). Harvests PubMed, Europe PMC, PubTator, and
  LitVar for a graph and deduplicates by identifier.
- **Evidence** (`src/query-test/evidence/`, LLM via OpenRouter). Screens papers, extracts
  entities and edges with verbatim quotes, checks the quotes against the text, normalizes
  IDs, and ranks candidate solutions as *direct* or *transfer*. Writes `*.kg.json`.
- **Discovery snapshot** (`garra.discovery.build_snapshot`). Reads cached Monarch and Open
  Targets packets from `data/cache/connections/` (written by `garra.atlas prefetch-packets`)
  plus any `runs/**/*.kg.json` evidence. Writes `data/derived/discovery/bridge.json`, which
  holds disease pairs, overlapping pathway groups, and complete-link phenotype clusters.
  Every edge keeps its provenance. `webapp/src/lib/body-regions.json` maps 40 body regions
  to HPO roots, so a disease falls in a region when one of its phenotypes is a descendant of
  that root.
- **Export** (`src/query-test/web_export.py`). Turns runs into the `present` (patient view)
  and `evidence` JSON and Markdown files that the graph pages read.

### 3. Serve

`python -m garra.ui` (`src/garra/ui/server.py`) is a loopback HTTP server on port 8787. It
loads the bridge, ontology, atlas, and community catalog into memory on startup.

| Route | Backed by |
|---|---|
| `GET /api/regions[/id]`, `/api/clusters[/id]`, `/api/entity`, `/api/neighbors` | discovery snapshot (in memory, offline) |
| `POST /api/search` | text search over bridge + atlas, or confirmed-HPO search via Monarch |
| `POST /api/research/search`, `/api/research/papers` | `garra.research` worker running query-test graph/literature, bounded (3 workers, 65 s) |
| `POST /api/research/atlas` | `data/hpo/hp.obo` + `phenotype.hpoa` |
| `GET/POST /api/graphs…` | queued background builds (`garra.ui.graphs`) in `data/web-graphs/` |
| `GET /api/communities` | `examples/communities.demo.json` |

The Next.js routes in `webapp/src/app/api/` proxy to this server from the server side, using
`GARRA_RESEARCH_URL`. Browsers never call Python directly. The assistant (`webapp/src/lib/ai.ts`)
gets read-only tools (`search_knowledge`, `find_contacts`, `find_papers`, `search_workspace`)
that go through these routes, and the retrieved records are shown as cited cards.
Private data such as accounts, notes, uploads, and chats lives in an AES-GCM-encrypted SQLite
database locally (`webapp/data/`), or in Supabase when hosted. It never reaches the Python
service.

**Hosted:** `scripts/export_hosted_discovery.py` gzips the bridge, `hp.obo`, `atlas.sqlite`,
and the region map into `deploy/discovery/`, which is tracked in git and has SHA256 checksums.
On Vercel, `scripts/prepare_hosted.py` verifies that bundle and unpacks it to `data/hosted/`,
and `api/index.py` serves the same routes behind `GARRA_RESEARCH_TOKEN`.

## Build the local datasets

Requires Python 3.12+ (not 3.14.1) and Node 22.13+. Run from the repo root. The examples use
POSIX paths; on Windows use `.venv\Scripts\python`.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[research]'
export PYTHONPATH=src
```

**Minimum to start the server.** `dev:all` refuses to start without `bridge.json` and
`hp.obo`. The quickest way to get them is to unpack the tracked bundle:

```bash
mkdir -p data/derived/discovery data/ontology data/hpo
gunzip -c deploy/discovery/bridge.json.gz  > data/derived/discovery/bridge.json
gunzip -c deploy/discovery/hp.obo.gz       > data/ontology/hp.obo
gunzip -c deploy/discovery/atlas.sqlite.gz > data/atlas.sqlite
cp data/ontology/hp.obo data/hpo/hp.obo
curl -Lo data/hpo/phenotype.hpoa https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa
```

**Full rebuild from source:**

```bash
python -m garra.sources                       # curated raw files -> data/raw/ (--allow-large for ClinVar)
python -m garra.atlas build                   # -> data/atlas.sqlite
python -m garra.local download                # ~24 GB -> data/local/raw/ (resumable)
python src/data/pubmed_bulk_download.py       # PubMed baseline -> data/pubmed-nih-gov/
python src/data/reporter-nih-gov.py           # RePORTER -> data/reporter-nih-gov/
python -m garra.local build                   # -> data/local/*.sqlite (~1.5 h)
python -m garra.atlas prefetch-packets        # Monarch/OT packets -> data/cache/connections/
python scripts/refresh_discovery.py --verify-genes  # bridge.json + deploy/discovery bundle
```

`python -m garra.local status` lists the index sizes. `--only <name>` limits any `garra.local`
command to one source. Each step is optional: whatever is missing gets answered by live APIs.

**Evidence graphs** (optional, need `OPENROUTER_API_KEY` in `.env`):

```bash
python src/query-test/main.py "Marfan syndrome" -o runs/marfan.json
python src/query-test/literature/main.py runs/marfan.json -o runs/marfan.papers.json
python src/query-test/evidence/main.py runs/marfan.papers.json -o runs/marfan.kg.json
python src/query-test/web_export.py runs/marfan.json -o webapp/public/graph-data
python scripts/refresh_discovery.py           # pull the new *.kg.json claims into the bridge
```

## Run the server

```bash
cp .env.example .env                          # NCBI_EMAIL, OPENROUTER_API_KEY
cd webapp
npm ci
cp .env.example .env.local                    # LLM key, Supabase URL + publishable key
npm run dev:all
```

`dev:all` (`webapp/scripts/dev-all.mjs`) starts `python -m garra.ui --bridge … --ontology …
--atlas … --communities …` on port 8787, waits for `/health`, and then starts Next.js on
**http://127.0.0.1:3000**. Sign-in needs Supabase Auth even though workspace storage is local
SQLite. To run the backend alone:

```bash
PYTHONPATH=src python -m garra.ui --port 8787 --atlas data/atlas.sqlite \
  --bridge data/derived/discovery/bridge.json --ontology data/ontology/hp.obo
```

Then open `/explore` in a browser.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
cd webapp && npm run typecheck && npm test
```

Both suites run on fixtures and mocks, so no datasets or network access are needed.

More detail: [LOCAL_DATA](docs/LOCAL_DATA.md), [BACKEND](docs/BACKEND.md),
[SEARCH-CLUSTERING](docs/SEARCH-CLUSTERING.md), [BODY-UI-INTEGRATION](docs/BODY-UI-INTEGRATION.md),
[COMMUNITY](docs/COMMUNITY.md), [webapp](webapp/README.md).
