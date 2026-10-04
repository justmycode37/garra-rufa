// Shapes of the two exported graph views (src/query-test/web_export.py) and their colours.

export type Link = { label: string; url: string };

// present.py: the patient-facing overview of one disease
export type PresentItem = {
  id: string; label: string; kind: string; section: string; group: string; group_id: string;
  notes: string[]; sources: string[]; links: Link[]; score: number; description?: string;
  frequency?: string; status?: string; phase?: number; country?: string; sponsors?: string[];
  variants?: Record<string, number>;
  // web_enrich.py: background facts from the local indexes
  freq?: number; similarity?: number; lay?: string; definition?: string; hallmark?: string;
  gene?: GeneInfo; drug?: DrugInfo; trial?: TrialInfo;
};
export type GeneInfo = {
  symbol: string; name?: string; locus?: string; function?: string; target_class?: string; tractable?: string[];
  mouse?: number; tissue_specificity?: string; tissues?: string[]; cell_location?: string[]; ot_score?: number;
};
export type DrugInfo = { chembl?: string; type?: string; stage?: string; mechanisms?: string[]; warnings?: string[] };
export type TrialInfo = {
  type?: string; phases?: string[]; start?: string; end?: string; acronym?: string; results?: boolean;
  why_stopped?: string; official_title?: string;
};
export type PhenotypeInfo = { lay?: string; definition?: string };
export type PresentView = {
  focus: { id: string; label: string; description: string | null; synonyms: string[]; links: Link[]; facts: { label: string; value: string }[] };
  sections: { id: string; label: string; color: string }[];
  groups: { id: string; label: string; section: string; items: string[]; count: number }[];
  items: Record<string, PresentItem>;
  links: { a: string; b: string; label: string; origin: string }[];
  notes: string[];
};

// evidence/main.py: the evidence graph of existing solutions
export type Evidence = { paper: string; level: string; effect: string; organism: string; passage: string; section: string; quote: string; year?: number };
export type EvidenceNode = {
  id: string; label: string; kind: string; how: string; names: string[]; xrefs: string[]; papers: string[];
  in_source_graph: boolean; score?: number; category?: string;
  gene?: GeneInfo; drug?: DrugInfo; phenotype?: PhenotypeInfo;
};
export type EvidenceEdge = {
  from: string; to: string; relation: string; level: string; confidence: number; papers: number;
  negative_papers: number; mostly_negative: boolean; evidence: Evidence[];
};
export type CandidatePath = {
  score: number; edge: string; level: string; confidence: number; papers: number; negative_papers: number;
  bridge: string; bridge_weight: number; evidence: string[]; bridge_evidence: string[];
};
export type Candidate = {
  id: string; label: string; kind: string; score: number; category: string; tested_without_benefit_in_input: boolean;
  found_by?: string[]; papers: string[]; paths: CandidatePath[]; n_papers?: number; best_level?: string | null;
  tier?: number; generic?: boolean; other_disease_endpoint?: boolean;
};
export type Gap = {
  intervention: string; intervention_label: string; mechanism_label: string; status: string; plausibility?: number | null;
  literature: { count: number | null; papers: string[] }; trials: unknown[]; reason?: string; rationale?: string;
  caveat?: string; experiment?: string;
};
export type Paper = {
  key: string; pmid?: string | null; pmcid?: string | null; doi?: string | null; title?: string; year?: number | string | null;
  journal?: string | null; cited_by?: number | null; text_source: string; summary: string; origin: string;
  // why it was screened in, and how well it was read
  relevance?: number; connection?: string; why?: string; solution_types?: string[];
  truncated?: boolean; chars?: number; quotes?: number; unverified?: number;
};
export type Screening = {
  screened: number; included: number; read: number; by_connection: Record<string, number>;
  unread: { key: string; title?: string; relevance?: number; connection?: string; why?: string }[];
};
export type Pipeline = {
  seconds?: number; text_sources?: Record<string, number>;
  extraction?: { entities?: number; edges?: number; quotes?: number; unverified_quotes?: number; edges_dropped_unverified?: number };
  normalization?: Record<string, number>;
};
export type EvidenceView = {
  start: string; disease: string; settings: { model?: string }; stats: { screened?: number; read?: number };
  nodes: EvidenceNode[]; edges: EvidenceEdge[]; candidates: Candidate[]; gaps: Gap[]; papers: Paper[];
  paper_links: { a: string; b: string; shared_edges: number; shared_entities: number }[];
  screening?: Screening; pipeline?: Pipeline;
};

// Overview topics in the webapp's palette (present.py's own colours are the fallback).
export const SECTION_COLORS: Record<string, string> = {
  symptoms: '#d59683', genetics: '#85a3d6', related: '#9d93d4', treatment: '#90986c',
  trials: '#d8bd52', support: '#e3a1b4', care: '#7fb7a9', research: '#b39f8f',
};
// Evidence entities come in families: each family is one hue of the webapp's palette,
// its kinds are shades of that hue, and the graph gives every family its own territory.
export type KindFamily = { id: string; label: string; color: string; kinds: string[]; shades: string[] };
export const KIND_FAMILIES: KindFamily[] = [
  { id: 'treatment', label: 'Treatments', color: '#7d8566', kinds: ['drug', 'therapy'], shades: ['#69745a', '#9aa28a'] },
  { id: 'clinical', label: 'Disease & symptoms', color: '#b58d80', kinds: ['disease', 'phenotype', 'anatomy'], shades: ['#a47868', '#b58d80', '#c4a0a6'] },
  { id: 'biology', label: 'Genes & biology', color: '#7d92b5', kinds: ['gene', 'pathway', 'process', 'cell_type'], shades: ['#667fa3', '#7d92b5', '#8f8bb0', '#9ea3bf'] },
  { id: 'measure', label: 'Measures', color: '#bda76a', kinds: ['biomarker', 'outcome_measure', 'diagnostic', 'assay'], shades: ['#a08d4f', '#bda76a', '#b09a7c', '#cdbf94'] },
  { id: 'research', label: 'Research tools', color: '#789e95', kinds: ['model_system', 'method', 'resource'], shades: ['#628a80', '#789e95', '#9bb8b1'] },
];
export const OTHER_FAMILY: KindFamily = { id: 'other', label: 'Other', color: '#a09488', kinds: [], shades: ['#a09488', '#85766a', '#bcb2a8'] };
export const familyOf = (kind: string) => KIND_FAMILIES.find(f => f.kinds.includes(kind)) ?? OTHER_FAMILY;
export const PAPER_COLOR = '#6b6664';
export const LEVELS = ['clinical_trial', 'registered_trial', 'observational', 'case_report', 'animal', 'in_vitro', 'in_silico', 'review', 'inferred'];
// strongest evidence in the deepest tones, weaker evidence fades towards the page
export const LEVEL_COLOR: Record<string, string> = {
  clinical_trial: '#5d7048', registered_trial: '#85906a', observational: '#627a9f', case_report: '#857ba8', animal: '#a77563',
  in_vitro: '#a08c4a', in_silico: '#8a7d72', review: '#b8b1ad', inferred: '#b98598', reports: '#dcd7d4', related: '#789e95',
};
export const NEGATIVE = '#a4504a';

export const safeUrl = (url: string | undefined | null) => (url && /^https?:\/\//i.test(url) ? url : undefined);
export const human = (s: string) => s.replaceAll('_', ' ');
export function paperUrl(p: Paper | undefined) {
  if (!p) return undefined;
  if (p.pmid) return `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(p.pmid)}/`;
  if (p.doi) return `https://doi.org/${p.doi}`;
  return undefined;
}
