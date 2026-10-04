import { handler, HttpError } from '@/lib/http';
import { graphBuildView, GraphBuildsUnavailable } from '@/lib/graph-builds';
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
