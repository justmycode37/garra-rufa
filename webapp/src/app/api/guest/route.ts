import { handler, HttpError } from '@/lib/http';

// Old guest cookies and the old creation endpoint cannot grant workspace access.
export const POST = handler(async () => { throw new HttpError(401, 'Sign in to open your workspace.'); });
