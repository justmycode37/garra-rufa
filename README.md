# garra-rufa

Rare-disease research atlas for Hack Nation.

## Web app

The Next.js frontend and AI application live in [`webapp/`](webapp/). From the repository root, with Node.js 22.13 or newer:

```sh
cd webapp
npm ci
cp -n .env.example .env.local
# Configure local credentials in .env.local, then:
npm run dev
```

Open http://127.0.0.1:3000. See the [web app README](webapp/README.md) for configuration and checks. This imports the existing app; connecting it to the Python search API remains a separate integration step. The Python backend commands below still run from the repository root.

## Data sources

Approved downloaders for the rare-disease atlas. Every fetch starts from a row in `src/garra/sources/catalog.py`. A file is kept only when the host is allowlisted, the body is larger than the empty-stub floor, and the expected header is present. The saved `manifest.json` records the publisher, license, canonical URL, resolved URL, time, byte size, and sha256.

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
