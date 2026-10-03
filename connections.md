# how we connect the graph

## ideas

semantic search
keyword search
citations
body location (needs dataset)
involved gene/protein similarity (needs datasets)

Connection priority (what edges mean)
Gene → pathway → another gene/disease (Monarch gene_to_pathway + gene–disease tables) — strongest
Disease ↔ HPO phenotypes (frequency / evidence when available) — strong
Same disease IDs / synonyms (Orphadata, MONDO alignments) — identity, not “similarity”
Studies / funding / papers (ClinicalTrials, RePORTER, PubMed) — actions, not biology clustering
Patient organizations — manual / Orphanet pages; no bulk download in our stack
