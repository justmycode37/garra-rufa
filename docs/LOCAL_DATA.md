# Local data

Most upstream APIs the atlas calls can be answered from local copies of their bulk
releases. The indexes live in `data/local/` (gitignored). When an index exists, the
request never leaves the machine. When it does not, or the request is one the index can't
answer, the code calls the live API exactly as before.

```bash
PYTHONPATH=src python -m garra.local download   # ~24 GB into data/local/raw/, resumable
PYTHONPATH=src python -m garra.local build      # all indexes (about 1.5 h, mostly PubMed)
PYTHONPATH=src python -m garra.local status     # sizes
PYTHONPATH=src python -m garra.local prune      # drop raw inputs the indexes consumed
```

`--only <name>` limits any command to one group or index. `build` prunes the inputs it
consumed unless `--keep-raw` is given. `GARRA_LOCAL=0` turns all local answers off.

To see which local queries are slow, log every handler call while a pipeline runs, then
replay the log:

```bash
GARRA_LOCAL_PROFILE=data/bench/run.tsv python src/query-test/main.py "Marfan syndrome" -o data/bench/marfan.json
PYTHONPATH=src python -m garra.local bench data/bench/run.tsv --repeat 2
```

## How it plugs in

`garra.local.router` holds one handler per API. Each handler emulates that API's response
shape, so the code that parses responses is unchanged:

- `query-test` sources: `Source.__init__` mounts a `requests` transport adapter
  (`sources/_local.py`).
- `query-test` literature/evidence providers: `Provider.fetch` asks the router first, then
  falls back to the throttle, the cache and the network.
- `garra.packet.fetch` and `garra.sources.queries`: the urllib helpers ask the router
  first.

## What is local

| API | Local index | Built from |
|---|---|---|
| EBI OLS4 (Mondo, DOID, Uberon, CL, GO, ChEBI, HP, FMA) | `ontology.sqlite` | OBO files, `fma.owl` |
| HGNC REST | `ontology.sqlite` | `hgnc_complete_set.txt` |
| JAX HPO API | `hpo.sqlite` + ontology | `phenotype.hpoa`, `genes_to_*` |
| NLM MeSH RDF (SPARQL, lookups) | `mesh.sqlite` | `desc2026.xml`, `supp2026` |
| Orphacode + Orphadata APIs | `orphadata.sqlite` | Orphadata XML products 1, 3, 4, 6, 7 |
| Monarch v3 search / entity / association | `monarch.sqlite` | `monarch-kg.tar.gz` (KGX) |
| Open Targets GraphQL | `opentargets.sqlite` | Platform 26.09 parquet |
| E-utilities, `db=clinvar` | `clinvar.sqlite` | `variant_summary`, `var_citations` |
| GTEx Portal v2 | `gtex.sqlite` | v10 median TPM, e/sQTL tars, lookup table |
| Human Protein Atlas | `hpa.sqlite` | `proteinatlas.json.gz` |
| ClinicalTrials.gov v2 `/studies` | `clinicaltrials.sqlite` | full JSON export |
| NIH RePORTER `/projects/search` | `reporter.sqlite` | ExPORTER in `data/reporter-nih-gov` |
| E-utilities, `db=pubmed` | `pubmed.sqlite` | baseline in `data/pubmed-nih-gov` |
| PubTator 3, LitVar 2 | `pubtator.sqlite` | PubTator 3 bulk files |

These still go to the network: Monarch semsim, Europe PMC (search, citations, full text),
PMC BioC full text, NORD, Orphanet expert-resource pages, Bright Data, and the LLM APIs.
RDCRN disease pages are cached once in `data/rdcrn/pages/`
(`python -m sources.rdcrn --prefetch` from `src/query-test`).

## Differences from the live APIs

**PubMed** holds the rare-disease subset (`build_pubmed.py`). A paper is kept if any of
these hold:

- one of its MeSH headings or supplementary concepts is a rare disease;
- its title names a rare disease;
- PubTator tags it with a rare disease *and* its abstract names one.

"Rare" means Orphanet disorders and Mondo's `rare` subset. A short `NOT_RARE` list removes
common conditions those sources include, such as gastric cancer and tuberculosis.

Indexing all of PubMed would need roughly 35–45 GB. `efetch`/`esummary` answer only when
every requested PMID is local; the literature providers send local and non-local PMIDs in
separate batches. Search ranking reads publication years from `pubmed_year.bin` (one
byte per PMID, written by the build), not from the paper table. Search terms map to FTS5
columns. `[MeSH Terms]` explodes to narrower
headings like PubMed does, but relevance is BM25 plus a recency bonus, not PubMed's Best
Match.

**PubTator**:

- Disease, gene and chemical annotations exist only for papers in the PubMed subset.
- Variant mentions cover all of PubMed.
- Search ranks papers that name every concept in the title or abstract first, then the
  newest.
- The bulk files have no mention counts, so each concept counts once per paper.

**LitVar**: rsIDs come from PubTator variant mentions. A gene's protein changes are matched
through ClinVar variant names.

**Open Targets**:

- Associations are the *direct* overall scores (the API defaults to indirect).
- At most 100 interactions and 50 baseline-expression rows per target.
- At most 10 Europe PMC papers per disease–target pair.

**GTEx**: keeps the 200 strongest variants per gene (best p-value over tissues) for eQTLs
and for sQTLs, so a variant lookup can miss weak QTLs.

**ClinVar**:

- One record per VariationID (GRCh38 preferred).
- Trait sets come from `PhenotypeIDS`.
- Molecular consequence is not in the bulk table.
- Search order is review status, then number of submitters.

**ClinicalTrials.gov** answers `query.term=AREA[ConditionMeshId]…`, `query.cond` and
free-text `query.term`. Other filters go to the API.

**RePORTER**:

- FY2015 onward.
- Only the latest application of each core project.
- Free-text search over titles and abstracts.

## Refreshing

Delete an index and run `build --only <name>`, or `download --only <group>` first to get a
new release. The `download` URLs point at "latest" releases except Open Targets
(`OT_RELEASE`). Each `data/local/raw/<group>/manifest.json` records what was fetched and
when.
