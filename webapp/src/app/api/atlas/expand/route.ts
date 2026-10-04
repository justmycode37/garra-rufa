import { handler, HttpError, json } from '@/lib/http';
import { expandAtlasDisease } from '@/lib/atlas-repository';
import { ResearchUnavailable } from '@/lib/research';
import { limit } from "@/lib/persistence";

export const maxDuration = 75;
export const GET = handler(async request => {
  const params = new URL(request.url).searchParams;
  const entity = params.get('entity') ?? '', label = (params.get('label') ?? '').trim();
  if (!/^(OMIM|ORPHA|MONDO|DECIPHER):\d+$/.test(entity) || label.length < 2 || label.length > 200 || /[\r\n\x00-\x1f]|:\/\//.test(label)) throw new HttpError(400, 'Select a disease record to explore its connections.');
  if (!(await limit('atlas:expand', 60, 10 * 60 * 1000))) throw new HttpError(429, 'Research is busy. Please try again shortly.');
  try { return json(await expandAtlasDisease(entity, label, request.signal)); }
  catch (error) { if (error instanceof ResearchUnavailable) throw new HttpError(503, error.message); throw error; }
});
