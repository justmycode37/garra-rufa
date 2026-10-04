import { handler, HttpError, json, requireUser } from '@/lib/http';
import { addGraphEvidence, graphBuildView, GraphBuildsUnavailable } from '@/lib/graph-builds';
import { limit } from '@/lib/persistence';

export const maxDuration = 60;
export const GET = handler(async request => {
  const [id, view] = new URL(request.url).pathname.split('/').slice(-2);
  if (!/^[a-z0-9-]{1,60}-[0-9a-f]{8}$/.test(id) || (view !== 'present' && view !== 'evidence')) throw new HttpError(404, 'Graph not found.');
  if (!(await limit('graphs:view', 600, 10 * 60 * 1000))) throw new HttpError(429, 'Graphs are busy. Please try again shortly.');
  try {
    const upstream = await graphBuildView(id, view);
    if (!upstream) throw new HttpError(404, 'This graph is not ready yet.');
    return new Response(upstream, { headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } });
  } catch (error) {
    if (error instanceof GraphBuildsUnavailable) throw new HttpError(503, error.message);
    throw error;
  }
});

// Adds the evidence graph to a graph built without it.
export const POST = handler(async request => {
  const [id, view] = new URL(request.url).pathname.split('/').slice(-2);
  if (!/^[a-z0-9-]{1,60}-[0-9a-f]{8}$/.test(id) || view !== 'evidence') throw new HttpError(404, 'Graph not found.');
  const user = await requireUser();
  if (user.guest) throw new HttpError(401, 'Please sign in to build the evidence graph.');
  // TEMPORARY: near-disabled limit for testing, as for builds (was: 3 per 60 * 60 * 1000)
  if (!(await limit(`graphs:build:${user.id}`, 1000, 1000))) throw new HttpError(429, 'You have started several graphs recently. Please try again later.');
  try {
    const result = await addGraphEvidence(id);
    if (!result) throw new HttpError(404, 'Graph not found.');
    if (result.error) throw new HttpError(400, result.error);
    return json({ build: result.build }, 202);
  } catch (error) {
    if (error instanceof GraphBuildsUnavailable) throw new HttpError(503, error.message);
    throw error;
  }
});
