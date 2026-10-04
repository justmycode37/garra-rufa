import { handler, json } from '@/lib/http';
import { listConditions } from '@/lib/conditions';

export const GET = handler(async () => json({ conditions: listConditions() }));
