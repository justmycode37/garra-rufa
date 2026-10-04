import { z } from 'zod';
import { body, handler, HttpError, json, requireUser } from '@/lib/http';
import { GraphBuildsUnavailable, listGraphBuilds, startGraphBuild } from '@/lib/graph-builds';
import { limit } from '@/lib/persistence';

const buildRequest = z.object({ query: z.string().trim().min(2).max(120), evidence: z.boolean().default(false) }).strict();

export const GET = handler(async () => {
  if (!(await limit('graphs:list', 600, 10 * 60 * 1000))) throw new HttpError(429, 'Graphs are busy. Please try again shortly.');
  try { return json(await listGraphBuilds()); }
  catch (error) {
    if (error instanceof GraphBuildsUnavailable) return json({ enabled: false, evidence: false, builds: [], notice: error.message });
    throw error;
  }
});

export const POST = handler(async request => {
  const user = await requireUser();
  if (user.guest) throw new HttpError(401, 'Please sign in to build a graph.');
  const { query, evidence } = buildRequest.parse(await body(request));
  if (/[\r\n\x00-\x1f]|:\/\//.test(query)) throw new HttpError(400, 'Use a disease, symptom, or gene name.');
  // Builds run the full pipelines (and LLM passes for evidence): keep them rare per person.
  if (!(await limit(`graphs:build:${user.id}`, evidence ? 3 : 8, 60 * 60 * 1000))) throw new HttpError(429, 'You have started several graphs recently. Please try again later.');
  try {
    const result = await startGraphBuild(query, evidence);
    if (result.error) throw new HttpError(400, result.error);
    return json({ build: result.build }, 202);
  } catch (error) {
    if (error instanceof GraphBuildsUnavailable) throw new HttpError(503, error.message);
    throw error;
  }
});
