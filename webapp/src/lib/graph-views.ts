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
export const KIND_PALETTE = ['#85a3d6', '#d59683', '#90986c', '#9d93d4', '#d8bd52', '#7fb7a9', '#e3a1b4', '#b39f8f',
  '#5f82bd', '#c07560', '#6f7c46', '#7c70bf', '#b89b33', '#5a9a8a', '#c87b95', '#8a7565', '#a8abd8'];
export const PAPER_COLOR = '#554f4d';
export const LEVELS = ['clinical_trial', 'observational', 'case_report', 'animal', 'in_vitro', 'in_silico', 'review', 'inferred'];
export const LEVEL_COLOR: Record<string, string> = {
  clinical_trial: '#16a34a', observational: '#2563eb', case_report: '#7c3aed', animal: '#ea580c',
  in_vitro: '#ca8a04', in_silico: '#64748b', review: '#94a3b8', inferred: '#db2777', reports: '#c9c4c2', related: '#0891b2',
};
export const NEGATIVE = '#dc2626';

export const safeUrl = (url: string | undefined | null) => (url && /^https?:\/\//i.test(url) ? url : undefined);
export const human = (s: string) => s.replaceAll('_', ' ');
export function paperUrl(p: Paper | undefined) {
  if (!p) return undefined;
  if (p.pmid) return `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(p.pmid)}/`;
  if (p.doi) return `https://doi.org/${p.doi}`;
  return undefined;
}
