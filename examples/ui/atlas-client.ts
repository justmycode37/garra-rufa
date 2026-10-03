/** Copy into the UI project. Region mesh names map to region.id, not HPO IDs. */
export interface Symptom {
  id: string;
  hpo_label: string;
  label: string;
  description: string;
  regions: string[];
  source_url: string;
}
export interface BodyMapCatalog {
  version: string;
  hpo_release: string;
  review_status: string;
  instructions: string;
  regions: { id: string; label: string }[];
  symptoms: Symptom[];
}
export interface ConnectionCard {
  id: string;
  name: string;
  why_shown: string;
  similarity: {
    metric: string; value: number; display_score: number | null;
    display_label: string; label: string; interpretation: string;
  };
  annotation_counts: { disease: number; query: number };
  matched_phenotypes: {
    query_id: string; query_label: string; disease_phenotype_id: string;
    disease_phenotype_label: string; score: number; exact: boolean;
  }[];
  claims: Record<string, unknown>[];
  assets: Record<string, unknown>[];
  papers: Record<string, unknown>[];
  evidence: Record<string, unknown>[];
  warnings: string[];
}
export interface SearchResponse {
  status: 'ok' | 'no_matches';
  message: string;
  cards: ConnectionCard[];
  notice: string;
  upstream: { retrieved_at: string; release: string; cache: { hit: boolean } };
}
const defaultBase = 'http://127.0.0.1:8787';
async function json<T>(response: Response): Promise<T> {
  const data = await response.json();
  if (!response.ok) throw new Error(data.message ?? data.error ?? 'Search failed');
  return data as T;
}
export function loadBodyMap(base = defaultBase, signal?: AbortSignal) {
  return fetch(`${base}/api/body-map`, { signal }).then(json<BodyMapCatalog>);
}
export function symptomsForRegion(catalog: BodyMapCatalog, regionId: string) {
  return catalog.symptoms.filter(symptom => symptom.regions.includes(regionId));
}
/** Call only after the user confirms the selected individual symptoms. */
export function searchConnections(hpoIds: string[], options: {
  base?: string; minScore?: number; limit?: number; signal?: AbortSignal;
} = {}) {
  return fetch(`${options.base ?? defaultBase}/api/connections/search`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: options.signal,
    body: JSON.stringify({ hpo_ids: [...new Set(hpoIds)], confirmed: true,
      metric: 'jaccard_similarity', min_score: options.minScore ?? 50, limit: options.limit ?? 5 }),
  }).then(json<SearchResponse>);
}
