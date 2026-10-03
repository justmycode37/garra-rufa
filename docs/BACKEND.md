# Backend (non-UI) — division of work

## Teammate: data fetch

Run from repo root:

```bash
PYTHONPATH=src python -m garra.sources
```

Files must land at `data/raw/<source_id>/<filename>` (see `garra.sources.catalog`).  
`data/raw/manifest.json` is updated automatically by the fetcher.

Check what is still missing:

```bash
PYTHONPATH=src python -m garra.atlas missing
```

## You: graph + logic (this package)

After any new fetch:

```bash
PYTHONPATH=src python -m garra.atlas build
```

Creates `data/atlas.sqlite` (gitignored). Re-run build whenever raw files change.

### CLI probes (for you + UI teammate)

```bash
PYTHONPATH=src python -m garra.atlas resolve "Pompe disease"
PYTHONPATH=src python -m garra.atlas similar GAA --limit 5
PYTHONPATH=src python -m garra.atlas journey "Pompe disease"
PYTHONPATH=src python -m garra.atlas journey "Pompe disease" --offline
```

### Python API for the UI

```python
from garra.graph.resolve import resolve_query
from garra.similarity import similar_diseases
from garra.actions.journey import build_journey
from garra.explain import explain_journey, explain_query

resolve_query("Pompe disease")
similar_diseases(gene_key="SYMBOL:GAA", limit=5)
build_journey("Pompe disease", live=True)

# OpenAI narrative (set OPENAI_API_KEY; falls back if missing or error)
explain_query("Pompe disease", live=True)
journey = build_journey("Pompe disease", live=False)
explain_journey(journey, use_openai=True)
```

CLI:

```bash
export OPENAI_API_KEY=sk-...
PYTHONPATH=src python3 -m garra.atlas explain "Pompe disease"
PYTHONPATH=src python3 -m garra.atlas explain "Pompe disease" --no-openai
```

All responses are JSON-serifiable dicts with `status`, evidence fields, and `warnings` where relevant.

## What improves when more raw files arrive

| Source | Enables |
|--------|---------|
| `hpo_disease_phenotype` | HPO overlap + `shared_phenotype` neighbors |
| `monarch_gene_to_pathway` | Pathway clustering + `shared_pathway` |
| Orphadata products | Orpha ids, synonyms, richer resolve |

No UI code lives here; import the functions above from Streamlit/React backend.
