// Shapes of the two exported graph views (src/query-test/web_export.py) and their colours.

export type Link = { label: string; url: string };

// present.py: the patient-facing overview of one disease
export type PresentItem = {
  id: string; label: string; kind: string; section: string; group: string; group_id: string;
  notes: string[]; sources: string[]; links: Link[]; score: number; description?: string;
  frequency?: string; status?: string; phase?: number; country?: string; sponsors?: string[];
  variants?: Record<string, number>;
};
export type PresentView = {
  focus: { id: string; label: string; description: string | null; synonyms: string[]; links: Link[]; facts: { label: string; value: string }[] };
  sections: { id: string; label: string; color: string }[];
  groups: { id: string; label: string; section: string; items: string[]; count: number }[];
  items: Record<string, PresentItem>;
  links: { a: string; b: string; label: string; origin: string }[];
  notes: string[];
};

// evidence/main.py: the evidence graph of existing solutions
export type Evidence = { paper: string; level: string; effect: string; organism: string; passage: string; section: string; quote: string };
export type EvidenceNode = {
  id: string; label: string; kind: string; how: string; names: string[]; xrefs: string[]; papers: string[];
  in_source_graph: boolean; score?: number; category?: string;
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
};
export type EvidenceView = {
  start: string; disease: string; settings: { model?: string }; stats: { screened?: number; read?: number };
  nodes: EvidenceNode[]; edges: EvidenceEdge[]; candidates: Candidate[]; gaps: Gap[]; papers: Paper[];
  paper_links: { a: string; b: string; shared_edges: number; shared_entities: number }[];
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
  { id: 'treatment', label: 'Treatments', color: '#90986c', kinds: ['drug', 'therapy'], shades: ['#6f7c46', '#a3ab7c'] },
  { id: 'clinical', label: 'Disease & symptoms', color: '#d59683', kinds: ['disease', 'phenotype', 'anatomy'], shades: ['#c07560', '#d59683', '#e3a1b4'] },
  { id: 'biology', label: 'Genes & biology', color: '#85a3d6', kinds: ['gene', 'pathway', 'process', 'cell_type'], shades: ['#5f82bd', '#85a3d6', '#9d93d4', '#a8abd8'] },
  { id: 'measure', label: 'Measures', color: '#d8bd52', kinds: ['biomarker', 'outcome_measure', 'diagnostic', 'assay'], shades: ['#b89b33', '#d8bd52', '#c9a26a', '#ddc985'] },
  { id: 'research', label: 'Research tools', color: '#7fb7a9', kinds: ['model_system', 'method', 'resource'], shades: ['#5a9a8a', '#7fb7a9', '#a5cbc1'] },
];
export const OTHER_FAMILY: KindFamily = { id: 'other', label: 'Other', color: '#b39f8f', kinds: [], shades: ['#b39f8f', '#8a7565', '#cbbcb0'] };
export const familyOf = (kind: string) => KIND_FAMILIES.find(f => f.kinds.includes(kind)) ?? OTHER_FAMILY;
export const PAPER_COLOR = '#554f4d';
export const LEVELS = ['clinical_trial', 'registered_trial', 'observational', 'case_report', 'animal', 'in_vitro', 'in_silico', 'review', 'inferred'];
// strongest evidence in the deepest tones, weaker evidence fades towards the page
export const LEVEL_COLOR: Record<string, string> = {
  clinical_trial: '#4f6a2f', registered_trial: '#7d8a4e', observational: '#4f72ad', case_report: '#7466b5', animal: '#b8664f',
  in_vitro: '#a88a26', in_silico: '#8a7565', review: '#b3aaa6', inferred: '#c87b95', reports: '#d9d3d0', related: '#5a9a8a',
};
export const NEGATIVE = '#b4372f';

export const safeUrl = (url: string | undefined | null) => (url && /^https?:\/\//i.test(url) ? url : undefined);
export const human = (s: string) => s.replaceAll('_', ' ');
export function paperUrl(p: Paper | undefined) {
  if (!p) return undefined;
  if (p.pmid) return `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(p.pmid)}/`;
  if (p.doi) return `https://doi.org/${p.doi}`;
  return undefined;
}
