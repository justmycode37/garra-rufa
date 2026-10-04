import { handler, HttpError, json } from '@/lib/http';
import { repositoryGraphSchema } from '@/lib/atlas-graph';
import { expandAtlasDisease } from '@/lib/atlas-repository';
import { ResearchUnavailable } from '@/lib/research';
import { limit } from "@/lib/persistence";

export const maxDuration = 75;
export const GET = handler(async request => {
  const params = new URL(request.url).searchParams;
  const entity = params.get('entity') ?? '', label = (params.get('label') ?? '').trim();
  if (!/^(OMIM|ORPHA|MONDO|DECIPHER):\d+$/.test(entity) || label.length < 2 || label.length > 200 || /[\r\n\x00-\x1f]|:\/\//.test(label)) throw new HttpError(400, 'Select a disease record to explore its connections.');
  if (!(await limit('atlas:expand', 60, 10 * 60 * 1000))) throw new HttpError(429, 'Research is busy. Please try again shortly.');
  try {
    const endpoint = new URL('/api/entity', process.env.GARRA_RESEARCH_URL || 'http://127.0.0.1:8787');
    endpoint.searchParams.set('id', entity);
    const response = await fetch(endpoint, {cache:'no-store',redirect:'error',signal:AbortSignal.any([request.signal,AbortSignal.timeout(20000)]), headers: process.env.GARRA_RESEARCH_TOKEN ? {Authorization:`Bearer ${process.env.GARRA_RESEARCH_TOKEN}`} : {}});
    if(response.ok){const data=await response.json();return json({graph:repositoryGraphSchema.parse(data.graph),unavailableProviders:data.unavailableProviders || []});}
    if(response.status!==404)throw new ResearchUnavailable('The loaded graph could not be reached. Please retry.');
    return json(await expandAtlasDisease(entity, label, request.signal));
  }
  catch (error) { if (error instanceof ResearchUnavailable) throw new HttpError(503, error.message); throw error; }
});
