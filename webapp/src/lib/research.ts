import { z } from 'zod';
import { sourceSchema } from './source-schema';
import { repositoryGraphSchema } from './atlas-graph';

const responseSchema = z.object({
  status: z.enum(['ok', 'partial', 'empty']), query: z.string().max(200),
  sources: z.array(sourceSchema).max(20), pipeline: z.enum(['repository-graph', 'repository-literature']),
  providers: z.array(z.string()).max(30), unavailableProviders: z.array(z.string()).max(30),
  retrievedAt: z.string(), cached: z.boolean(), notice: z.string().optional(),
  graph: repositoryGraphSchema.optional(),
});
export type ResearchResponse = z.infer<typeof responseSchema>;
export class ResearchUnavailable extends Error {}

export async function searchResearch(query: string, options: { papers?: boolean; contacts?: boolean; limit?: number; signal?: AbortSignal } = {}): Promise<ResearchResponse> {
  const term = query.trim();
  if (term.length < 2 || term.length > 200 || /[\r\n\x00-\x1f]|:\/\//.test(term)) throw new ResearchUnavailable('Use a short condition, gene, or research term.');
  let base: URL;
  try { base = new URL(process.env.GARRA_RESEARCH_URL || 'http://127.0.0.1:8787'); }
  catch { throw new ResearchUnavailable('The research service configuration is invalid.'); }
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) throw new ResearchUnavailable('The research service configuration is invalid.');
  // The service origin is server configuration. User/model text never selects a URL.
  const endpoint = new URL(options.papers ? '/api/research/papers' : '/api/research/search', base);
  try {
    const response = await fetch(endpoint, { method: 'POST', redirect: 'error', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', ...(process.env.GARRA_RESEARCH_TOKEN ? { Authorization: `Bearer ${process.env.GARRA_RESEARCH_TOKEN}` } : {}) },
      body: JSON.stringify({ query: term, limit: options.limit ?? 12, category: options.contacts ? 'contacts' : 'all' }),
      signal: AbortSignal.any([AbortSignal.timeout(68000), ...(options.signal ? [options.signal] : [])]),
    });
    if (!response.ok) throw new ResearchUnavailable('The research service is temporarily unavailable. Please retry.');
    const data = responseSchema.parse(await response.json());
    if (data.pipeline !== (options.papers ? 'repository-literature' : 'repository-graph')) throw new Error('Unexpected pipeline');
    // Private workspace links are never accepted from the research backend.
    if (data.sources.some(s => s.kind === 'workspace' || !/^https?:\/\//.test(s.url))) throw new Error('Unexpected private source');
    return data;
  } catch (error) {
    if (options.signal?.aborted) throw error;
    if (error instanceof ResearchUnavailable) throw error;
    throw new ResearchUnavailable('The research service could not be reached. Please try again shortly.');
  }
}

export function researchWarning(result: ResearchResponse) {
  return result.unavailableProviders.length ? `Some sources could not be reached (${result.unavailableProviders.join(', ')}). The cards show the records that were retrieved.` : undefined;
}
