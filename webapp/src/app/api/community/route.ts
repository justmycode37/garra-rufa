import { z } from 'zod';
import { handler, json, body, requireUser, HttpError } from '@/lib/http';
import { addCommunityPost, communityData, ConditionError, deleteCommunityPost, leaveCommunity, saveCommunityProfile } from "@/lib/persistence";

const profileSchema = z.object({ action: z.literal('profile').optional(), alias: z.string().trim().min(2).max(50), diseaseId: z.string().max(80).optional(), conditionName: z.string().trim().min(2).max(120).optional(), bio: z.string().trim().max(300) });
const postSchema = z.object({ action: z.literal('post'), conditionId: z.string().min(1).max(80), content: z.string().trim().min(1).max(4000) });
function run<T>(work: () => T): T { try { return work(); } catch (error) { if (error instanceof ConditionError) throw new HttpError(error.status, error.message); throw error; } }

export const GET = handler(async (req) => {
  const user = await requireUser();
  const conditionId = new URL(req.url).searchParams.get('conditionId') || undefined;
  if (conditionId && conditionId.length > 80) throw new HttpError(400, 'Choose a valid community.');
  return json(run(async () => (await communityData(user?.id, conditionId))));
});
export const POST = handler(async (req) => {
  const user = await requireUser();
  const input = z.union([postSchema, profileSchema]).parse(await body(req));
  if (input.action === 'post') return json({ post: run(async () => (await addCommunityPost(user.id, input.conditionId, input.content))) });
  return json({ ok: true, ...run(async () => (await saveCommunityProfile(user.id, input))) });
});
export const DELETE = handler(async (req) => {
  const user = await requireUser();
  // The original no-body request still leaves the opt-in member directory.
  const text = await req.text();
  if (!text.trim()) { (await leaveCommunity(user.id)); return json({ ok: true }); }
  if (text.length > 200000) throw new HttpError(413, 'This request is too large.');
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new HttpError(400, 'Please send a valid request.'); }
  const input = z.union([z.object({ postId: z.uuid() }).strict(), z.object({ conditionId: z.string().min(1).max(80) }).strict()]).parse(value);
  if ('postId' in input) run(async () => (await deleteCommunityPost(user.id, input.postId)));
  else (await leaveCommunity(user.id, input.conditionId));
  return json({ ok: true });
});
