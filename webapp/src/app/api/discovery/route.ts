import { handler, body, HttpError, json } from '@/lib/http';

// Fixed server-side routes: the browser cannot choose an upstream host or URL.
const paths: Record<string, string> = { regions: '/api/regions', region: '/api/regions/', clusters: '/api/clusters', cluster: '/api/clusters/', search: '/api/search', neighbors: '/api/neighbors', communities: '/api/communities' };
async function proxy(request: Request) {
  const params = new URL(request.url).searchParams;
  const resource = params.get('resource') || 'regions';
  if (!(resource in paths) || (request.method === 'POST') !== (resource === 'search')) throw new HttpError(400, 'Invalid discovery operation.');
  const id = params.get('id') || '';
  if (['region', 'cluster'].includes(resource) && !/^[a-z0-9_-]{1,100}$/.test(id)) throw new HttpError(400, 'Invalid discovery identifier.');
  const base = new URL(process.env.GARRA_RESEARCH_URL || 'http://127.0.0.1:8787');
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) throw new HttpError(503, 'Invalid research service configuration.');
  const endpoint = new URL(paths[resource] + (['region','cluster'].includes(resource) ? id : ''), base);
  if (['clusters','neighbors'].includes(resource) && params.has('entity_id')) endpoint.searchParams.set('entity_id', params.get('entity_id')!);
  if (resource === 'communities') {
    for (const key of ['kind', 'disease_id', 'process_id']) {
      const value = params.get(key);
      if (value) endpoint.searchParams.set(key, value);
    }
  }
  const payload = request.method === 'POST' ? await body(request) : undefined;
  try {
    const response = await fetch(endpoint, {method: request.method, cache: 'no-store', redirect: 'error',
      headers: {'Content-Type': 'application/json', ...(process.env.GARRA_RESEARCH_TOKEN ? {Authorization: `Bearer ${process.env.GARRA_RESEARCH_TOKEN}`} : {})},
      body: payload === undefined ? undefined : JSON.stringify(payload), signal: AbortSignal.any([request.signal, AbortSignal.timeout(20000)])});
    const data = await response.json();
    if (!response.ok) throw new HttpError(response.status >= 500 ? 503 : response.status, data.message || data.error || 'Discovery unavailable.');
    return json(data);
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(503, 'Discovery backend is unavailable. Start npm run dev:all and retry.');
  }
}
export const GET = handler(proxy);
export const POST = handler(proxy);
