SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_manifest (
  source_id TEXT PRIMARY KEY,
  retrieved_at TEXT,
  publisher TEXT,
  license TEXT,
  source_url TEXT,
  sha256 TEXT
);

CREATE TABLE IF NOT EXISTS disease (
  disease_key TEXT PRIMARY KEY,
  primary_name TEXT NOT NULL,
  namespace TEXT NOT NULL,
  code TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS disease_alias (
  alias_normalized TEXT NOT NULL,
  disease_key TEXT NOT NULL,
  PRIMARY KEY (alias_normalized, disease_key),
  FOREIGN KEY (disease_key) REFERENCES disease(disease_key)
);

CREATE INDEX IF NOT EXISTS idx_disease_alias ON disease_alias(alias_normalized);

CREATE TABLE IF NOT EXISTS gene (
  gene_key TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  ncbi_id TEXT,
  hgnc_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_gene_symbol ON gene(symbol);

CREATE TABLE IF NOT EXISTS gene_alias (
  alias_normalized TEXT NOT NULL,
  gene_key TEXT NOT NULL,
  PRIMARY KEY (alias_normalized, gene_key),
  FOREIGN KEY (gene_key) REFERENCES gene(gene_key)
);

CREATE INDEX IF NOT EXISTS idx_gene_alias ON gene_alias(alias_normalized);

CREATE TABLE IF NOT EXISTS pathway (
  pathway_key TEXT PRIMARY KEY,
  label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phenotype (
  hpo_id TEXT PRIMARY KEY,
  label TEXT
);

CREATE TABLE IF NOT EXISTS edge_gene_disease (
  gene_key TEXT NOT NULL,
  disease_key TEXT NOT NULL,
  association_type TEXT,
  source_id TEXT NOT NULL,
  assertion TEXT NOT NULL DEFAULT 'curated',
  PRIMARY KEY (gene_key, disease_key, source_id),
  FOREIGN KEY (gene_key) REFERENCES gene(gene_key),
  FOREIGN KEY (disease_key) REFERENCES disease(disease_key)
);

CREATE TABLE IF NOT EXISTS edge_disease_phenotype (
  disease_key TEXT NOT NULL,
  hpo_id TEXT NOT NULL,
  frequency TEXT,
  source_id TEXT NOT NULL,
  PRIMARY KEY (disease_key, hpo_id, source_id),
  FOREIGN KEY (disease_key) REFERENCES disease(disease_key),
  FOREIGN KEY (hpo_id) REFERENCES phenotype(hpo_id)
);

CREATE TABLE IF NOT EXISTS edge_gene_pathway (
  gene_key TEXT NOT NULL,
  pathway_key TEXT NOT NULL,
  source_id TEXT NOT NULL,
  PRIMARY KEY (gene_key, pathway_key, source_id),
  FOREIGN KEY (gene_key) REFERENCES gene(gene_key),
  FOREIGN KEY (pathway_key) REFERENCES pathway(pathway_key)
);
"""
