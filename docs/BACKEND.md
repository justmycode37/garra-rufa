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

### Live connections packet (one anchor, cached — not a 7200-disease loop)

Fetches Monarch semsim + Orphadata phenotypes/genes + Open Targets pathways for **one** resolved anchor, writes cache under `data/cache/connections/<MONDO_id>/`, and emits connections packet schema v1:

```bash
PYTHONPATH=src python3 -m garra.atlas packet "Pompe disease" --candidate-limit 5
PYTHONPATH=src python3 -m garra.atlas packet "Pompe disease" --no-cache   # refetch APIs
```

End-to-end demo path (packet → `build_connections` → `explain` with connection cards merged into evidence):

```bash
PYTHONPATH=src python3 -m garra.atlas pipeline "Pompe disease" \
  --offline --no-openai --html connections.html
PYTHONPATH=src python3 -m garra.atlas pipeline "Pompe disease" --out examples/pompe.pipeline.json
```

Offline card rendering from any saved packet:

```bash
PYTHONPATH=src python3 -m garra.atlas connections data/cache/connections/MONDO_0009290/packet.json --html connections.html
```

See `docs/CONNECTIONS.md` for the fetch contract; implementation lives in `garra.packet`.

### Batch offline prefetch (curated anchors only)

Pre-build packets under `data/cache/connections/` for demo or ERN anchor lists — **not** a blind loop over all ~7200 RARe-SOURCE rows.

```bash
# Default: examples/anchors.txt (~10 demo diseases)
PYTHONPATH=src python3 -m garra.atlas prefetch-packets --dry-run

PYTHONPATH=src python3 -m garra.atlas prefetch-packets --sleep 1.5

# Custom list
PYTHONPATH=src python3 -m garra.atlas prefetch-packets --file my_anchors.txt

# Optional slice of RARe-SOURCE JSONL (hard cap 500; default limit 50)
PYTHONPATH=src python3 -m garra.atlas prefetch-packets \
  --raresource-jsonl data/raw/raresource/diseases.jsonl --limit 20 --offset 0

# Skip anchors that already have packet.json (--refetch to force)
PYTHONPATH=src python3 -m garra.atlas prefetch-packets --refetch

# Larger RARe-SOURCE slice (default 100; max 7200 with --full-catalog)
PYTHONPATH=src python3 -m garra.atlas prefetch-packets \
  --raresource-jsonl data/raw/raresource/diseases.jsonl --limit 200 --offset 0 --sleep 0.8

# Registry + stats (zero-network skip when query is known and packet complete)
PYTHONPATH=src python3 -m garra.atlas cache-rebuild-registry
PYTHONPATH=src python3 -m garra.atlas cache-status
```

Writes `data/cache/connections/prefetch_report.json`, `query_registry.json`, and per-anchor caches. Complete packets skip all network I/O on repeat runs. Later searches use cache via `packet` / `pipeline` / `connections …/packet.json`.

### Python API for the UI

```python
from garra.graph.resolve import resolve_query
from garra.similarity import similar_diseases
from garra.actions.journey import build_journey
from garra.explain import explain_journey, explain_query
from garra.packet import build_connections_packet, run_pipeline
from garra.connections import build_connections

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

## Ambiguous queries and local similarity

Resolvers and gene-neighbor searches return `status: ambiguous` with candidate matches instead of silently choosing the first disease. Ask the user to select a stable identifier, then repeat the query. Local weighted overlap is an unvalidated discovery heuristic; derived connections are labeled inferred and cannot receive high biological confidence from pathway overlap alone. The body-map API uses the separate Monarch adapter described in `UI-BRIDGE.md`.
