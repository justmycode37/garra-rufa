import { z } from 'zod';
import { body, handler, HttpError, json, requireUser } from '@/lib/http';
import { GraphBuildsUnavailable, listGraphBuilds, startGraphBuild } from '@/lib/graph-builds';
import { limit } from '@/lib/persistence';

// label: a readable name for the build, e.g. "Heart: chest pain" from the atlas
const buildRequest = z.object({ query: z.string().trim().min(2).max(120), evidence: z.boolean().default(false), label: z.string().trim().min(2).max(200).optional() }).strict();

// What can be built, and every graph built so far that has a view to open (the graph
// picker offers them all next to the bundled examples).
export const GET = handler(async () => {
  if (!(await limit('graphs:list', 600, 10 * 60 * 1000))) throw new HttpError(429, 'Graphs are busy. Please try again shortly.');
  try {
    const { enabled, evidence, builds } = await listGraphBuilds();
    return json({ enabled, evidence, builds: builds.filter(b => b.views.present || b.views.evidence) });
  } catch (error) {
    if (error instanceof GraphBuildsUnavailable) return json({ enabled: false, evidence: false, builds: [], notice: error.message });
    throw error;
  }
});

export const POST = handler(async request => {
  const user = await requireUser();
  if (user.guest) throw new HttpError(401, 'Please sign in to build a graph.');
  const { query, evidence, label } = buildRequest.parse(await body(request));
  if (/[\r\n\x00-\x1f]|:\/\//.test(query)) throw new HttpError(400, 'Use a disease, symptom, or gene name.');
  if (label && (/[\r\n\x00-\x1f]|:\/\//.test(label))) throw new HttpError(400, 'Use a plain-text graph name.');
  // Builds run the full pipelines (and LLM passes for evidence): keep them rare per person.
  // TEMPORARY: near-disabled limit for testing (was: evidence ? 3 : 8 per 60 * 60 * 1000)
  if (!(await limit(`graphs:build:${user.id}`, 1000, 1000))) throw new HttpError(429, 'You have started several graphs recently. Please try again later.');
  try {
    const result = await startGraphBuild(query, evidence, label);
    if (result.error) throw new HttpError(400, result.error);
    return json({ build: result.build }, 202);
  } catch (error) {
    if (error instanceof GraphBuildsUnavailable) throw new HttpError(503, error.message);
    throw error;
  }
});
