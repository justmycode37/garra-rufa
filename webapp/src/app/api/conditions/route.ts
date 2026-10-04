import { handler, json } from '@/lib/http';
import { listConditions } from "@/lib/persistence";

export const GET = handler(async () => json({ conditions: (await listConditions()) }));
