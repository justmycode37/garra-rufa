<<<<<<< HEAD
# garra-rufa
Hack Nation Hackathon
=======
# garra-rufa sources

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

UI teammate should import `resolve_query`, `similar_diseases`, and `build_journey` from `garra`.
>>>>>>> 6586fa1 (Add atlas backend: sources, ingest, similarity, journey, and OpenAI explain.)
