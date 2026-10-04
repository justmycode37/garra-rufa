import { handler, json, HttpError } from '@/lib/http';
import { researchWarning, searchResearch, ResearchUnavailable } from '@/lib/research';
import { limit } from "@/lib/persistence";

export const maxDuration = 75;
export const GET = handler(async request => {
  const query = (new URL(request.url).searchParams.get('q') || '').trim();
  if (query.length < 2 || query.length > 200) throw new HttpError(400, 'Enter a condition, gene, or research term (2–200 characters).');
  if (!(await limit('papers:public', 90, 10 * 60 * 1000))) throw new HttpError(429, 'Research is busy. Please try again shortly.');
  try {
    const result = await searchResearch(query, { papers: true, limit:16, signal:request.signal });
    return json({ ...result, warning:researchWarning(result) });
  } catch (error) {
    if (error instanceof ResearchUnavailable) throw new HttpError(503, error.message);
    throw error;
  }
});
