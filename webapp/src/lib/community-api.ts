import { z } from 'zod';
import { handler, json, body, HttpError } from './http';
import { addCommunityPost, communityData, ConditionError, deleteCommunityPost, leaveCommunity, saveCommunityProfile } from './persistence';
import type { User } from './types';

const profileSchema = z.object({ action: z.literal('profile').optional(), alias: z.string().trim().min(2).max(50), diseaseId: z.string().max(80).optional(), conditionName: z.string().trim().min(2).max(120).optional(), bio: z.string().trim().max(300) });
const postSchema = z.object({ action: z.literal('post'), conditionId: z.string().min(1).max(80), content: z.string().trim().min(1).max(4000) });

// Await inside the error boundary so rejected database operations keep their
// intended HTTP status, and finish before a success response is serialized.
async function run<T>(work: () => Promise<T>): Promise<T> {
  try { return await work(); }
  catch (error) {
    if (error instanceof ConditionError) throw new HttpError(error.status, error.message);
    throw error;
  }
}

export function createCommunityHandlers(requireUser: () => Promise<User>) {
  return {
    GET: handler(async req => {
      const user = await requireUser();
      const conditionId = new URL(req.url).searchParams.get('conditionId') || undefined;
      if (conditionId && conditionId.length > 80) throw new HttpError(400, 'Choose a valid community.');
      return json(await run(() => communityData(user.id, conditionId)));
    }),
    POST: handler(async req => {
      const user = await requireUser();
      const input = z.union([postSchema, profileSchema]).parse(await body(req));
      if (input.action === 'post') return json({ post: await run(() => addCommunityPost(user.id, input.conditionId, input.content)) });
      return json({ ok: true, ...await run(() => saveCommunityProfile(user.id, input)) });
    }),
    DELETE: handler(async req => {
      const user = await requireUser();
      // Preserve the original no-body request that leaves all communities.
      const text = await req.text();
      if (!text.trim()) {
        await run(() => leaveCommunity(user.id));
        return json({ ok: true });
      }
      if (text.length > 200000) throw new HttpError(413, 'This request is too large.');
      let value: unknown;
      try { value = JSON.parse(text); } catch { throw new HttpError(400, 'Please send a valid request.'); }
      const input = z.union([z.object({ postId: z.uuid() }).strict(), z.object({ conditionId: z.string().min(1).max(80) }).strict()]).parse(value);
      if ('postId' in input) await run(() => deleteCommunityPost(user.id, input.postId));
      else await run(() => leaveCommunity(user.id, input.conditionId));
      return json({ ok: true });
    }),
  };
}
