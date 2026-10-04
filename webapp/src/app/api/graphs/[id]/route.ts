import { handler, HttpError, json } from '@/lib/http';
import { getGraphBuild, GraphBuildsUnavailable } from '@/lib/graph-builds';

export const GET = handler(async request => {
  const id = new URL(request.url).pathname.split('/').at(-1) ?? '';
  if (!/^[a-z0-9-]{1,60}-[0-9a-f]{8}$/.test(id)) throw new HttpError(404, 'Graph not found.');
  try {
    const build = await getGraphBuild(id);
    if (!build) throw new HttpError(404, 'Graph not found.');
    return json({ build });
  } catch (error) {
    if (error instanceof GraphBuildsUnavailable) throw new HttpError(503, error.message);
    throw error;
  }
});
