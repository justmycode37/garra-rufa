import { z } from 'zod';

// Graphs built from a query by the research service (src/garra/ui/graphs.py).
export const graphBuildSchema = z.object({
  id: z.string().regex(/^[a-z0-9-]{1,60}-[0-9a-f]{8}$/), query: z.string().max(200), label: z.string().max(300),
  evidence: z.boolean(), state: z.enum(['queued', 'running', 'done', 'failed']), stage: z.string().nullable(),
  stages: z.array(z.string()).max(10), done: z.array(z.string()).max(10), error: z.string().nullable(),
  created: z.number(), updated: z.number(),
  views: z.object({ present: z.boolean().optional(), evidence: z.boolean().optional() }).passthrough(),
  log: z.array(z.string()).max(10),
});
export const graphBuildListSchema = z.object({ enabled: z.boolean(), evidence: z.boolean(), builds: z.array(graphBuildSchema).max(50) });
export type GraphBuild = z.infer<typeof graphBuildSchema>;
export type GraphBuildList = z.infer<typeof graphBuildListSchema>;
export class GraphBuildsUnavailable extends Error {}

function endpoint(path: string) {
  let base: URL;
  try { base = new URL(process.env.GARRA_RESEARCH_URL || 'http://127.0.0.1:8787'); }
  catch { throw new GraphBuildsUnavailable('The research service configuration is invalid.'); }
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) throw new GraphBuildsUnavailable('The research service configuration is invalid.');
  return new URL(path, base);
}

async function call(path: string, init: RequestInit = {}, timeout = 15000) {
  let response: Response;
  try {
    response = await fetch(endpoint(path), { ...init, redirect: 'error', cache: 'no-store',
      headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...(process.env.GARRA_RESEARCH_TOKEN ? { Authorization: `Bearer ${process.env.GARRA_RESEARCH_TOKEN}` } : {}) },
      signal: AbortSignal.timeout(timeout) });
  } catch (error) {
    if (error instanceof GraphBuildsUnavailable) throw error;
    throw new GraphBuildsUnavailable('The research service could not be reached.');
  }
  // The hosted research service has no build worker: it answers 404 / 503 here.
  if (response.status === 404 && path === '/api/graphs') throw new GraphBuildsUnavailable('Building graphs needs the local research service of this version (restart npm run dev:all).');
  if (response.status === 503) throw new GraphBuildsUnavailable('Building graphs is not enabled on the research service.');
  return response;
}

export async function listGraphBuilds(): Promise<GraphBuildList> {
  const response = await call('/api/graphs');
  if (!response.ok) throw new GraphBuildsUnavailable('Graph builds could not be loaded.');
  return graphBuildListSchema.parse(await response.json());
}

export async function getGraphBuild(id: string): Promise<GraphBuild | null> {
  const response = await call(`/api/graphs/${encodeURIComponent(id)}`);
  if (response.status === 404) return null;
  if (!response.ok) throw new GraphBuildsUnavailable('This graph build could not be loaded.');
  return graphBuildSchema.parse(await response.json());
}

export async function startGraphBuild(query: string, evidence: boolean): Promise<{ build?: GraphBuild; error?: string }> {
  const response = await call('/api/graphs/build', { method: 'POST', body: JSON.stringify({ query, evidence }) });
  const data = await response.json().catch(() => ({}));
  if (response.status === 400) return { error: typeof data.message === 'string' ? data.message.slice(0, 200) : 'Please check the query.' };
  if (!response.ok) throw new GraphBuildsUnavailable('The graph build could not be started.');
  return { build: graphBuildSchema.parse(data) };
}

/** The view JSON as text. Read in full and retried once: the stdlib backend sometimes
 * drops a large response mid-transfer on Windows. */
export async function graphBuildView(id: string, view: 'present' | 'evidence'): Promise<string | null> {
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await call(`/api/graphs/${encodeURIComponent(id)}/${view}`, {}, 60000);
      if (response.status === 404) return null;
      if (!response.ok) throw new GraphBuildsUnavailable('This graph could not be loaded.');
      return await response.text();
    } catch (error) {
      if (attempt >= 1 || error instanceof GraphBuildsUnavailable && !/reached/.test(error.message)) throw error instanceof GraphBuildsUnavailable ? error : new GraphBuildsUnavailable('This graph could not be loaded.');
    }
  }
}
