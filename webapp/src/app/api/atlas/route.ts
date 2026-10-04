import { handler, HttpError, json } from '@/lib/http';
import { atlasRegions } from '@/lib/atlas-graph';
import { loadAtlasRegion } from '@/lib/atlas-repository';
import { ResearchUnavailable } from '@/lib/research';
import { limit } from "@/lib/persistence";

export const GET = handler(async request => {
  const params = new URL(request.url).searchParams;
  const region = atlasRegions[params.get('region') ?? ''];
  const offset = Number(params.get('offset') ?? 0);
  const query = (params.get('q') ?? '').trim();
  if (!region || !Number.isInteger(offset) || offset < 0 || offset > 100000 || query.length > 120) throw new HttpError(400, 'Choose a body region and a valid record filter.');
  if (!(await limit('atlas:public', 300, 10 * 60 * 1000))) throw new HttpError(429, 'Atlas is busy. Please try again shortly.');
  try { return json(await loadAtlasRegion(region.hpo, offset, query, request.signal)); }
  catch (error) { if (error instanceof ResearchUnavailable) throw new HttpError(503, error.message); throw error; }
});
