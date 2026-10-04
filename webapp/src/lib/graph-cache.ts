// The Graphs page shows only the graphs this browser has queried, most recent first. The
// research service keeps the builds as a cache by query, the bundled example graphs
// (public/graph-data) answer a query for their disease without a build.

export const CACHE_KEY = 'garra:graph-runs';
export const CACHE_SIZE = 12;
/** A built graph (`marfan-syndrome-1a2b3c4d`) or a bundled example (`pompe`). */
const RUN_ID = /^[a-z0-9-]{1,60}$/;
export const isBuildId = (id: string) => /^[a-z0-9-]{1,60}-[0-9a-f]{8}$/.test(id);

export type ExampleEntry = { id: string; label: string; present: string | null; evidence: string | null; ids?: string[]; names?: string[] };

export function readCache(raw: string | null): string[] {
  try {
    const ids = JSON.parse(raw ?? '[]');
    return Array.isArray(ids) ? [...new Set(ids.filter((id): id is string => typeof id === 'string' && RUN_ID.test(id)))].slice(0, CACHE_SIZE) : [];
  } catch { return []; }
}

export const remember = (ids: string[], id: string) => RUN_ID.test(id) ? [id, ...ids.filter(x => x !== id)].slice(0, CACHE_SIZE) : ids;
export const forget = (ids: string[], id: string) => ids.filter(x => x !== id);

const normal = (text: string) => text.toLowerCase().replace(/[\s_-]+/g, ' ').trim();

/** The example graph a query names: its id, label, one of its names or disease ids. */
export function cachedExample(examples: ExampleEntry[], query: string, evidence = false) {
  const q = normal(query);
  return examples.find(e => e.present && (!evidence || e.evidence) && [e.id, e.label, ...(e.names ?? []), ...(e.ids ?? [])].some(n => normal(n) === q));
}
