import { randomBytes } from 'node:crypto';
import { cookies } from 'next/headers';
import { z } from 'zod';
import { body, currentUser, handler, HttpError, json, requireUser } from '@/lib/http';
import { limit, sessionUser } from "@/lib/persistence";
import { conversationTransferSchema } from '@/lib/chat-transfer';
import { accountForUser, browserAccounts, saveAccount } from "@/lib/persistence";
import { chatGPTRequestUrl, localChatGPTOrigin, startChatGPTSignIn } from '@/lib/chatgpt';

export const runtime = 'nodejs';
export const GET = handler(async req => {
  let available = true;
  try { localChatGPTOrigin(chatGPTRequestUrl(req)); } catch { available = false; }
  const browser = (await cookies()).get('garra_chatgpt_browser')?.value || '';
  return json({ available, accounts: (await browserAccounts(browser)).map(a => ({ id: a.id, name: a.name, email: a.email, label: `${a.email || a.name} · ${a.clientId.slice(-6)}` })) });
});

export const POST = handler(async req => {
  const input = (await z.object({ role: z.enum(['researcher', 'doctor', 'patient']), accountId: z.uuid().optional(), enablePlan: z.boolean().optional(), conversation: conversationTransferSchema.omit({ chatId: true }).optional() }).parseAsync(await body(req)));
  const c = await cookies();
  const browserId = c.get('garra_chatgpt_browser')?.value || randomBytes(32).toString('base64url');
  if (!(await limit('chatgpt:sign-in', 40, 10 * 60000))) throw new HttpError(429, 'Too many sign-in attempts. Please try again later.');
  try {
    const result = await startChatGPTSignIn({ ...input, requestUrl: chatGPTRequestUrl(req), browserId, current: (await sessionUser(c.get('garra_session')?.value || '')) });
    c.set('garra_chatgpt_browser', browserId, { httpOnly: true, sameSite: 'lax', path: '/', maxAge: 365 * 86400 });
    c.set('garra_chatgpt_attempt', result.attemptId, { httpOnly: true, sameSite: 'lax', path: '/api/auth/chatgpt', maxAge: 600 });
    return json({ url: result.url });
  } catch (error) {
    throw new HttpError(400, error instanceof Error && error.name === 'Error' ? error.message : 'ChatGPT sign-in is temporarily unavailable. Please try again.');
  }
});

export const PATCH = handler(async req => {
  const user = await requireUser();
  (await z.object({ action: z.literal('acknowledge-plan') }).parseAsync(await body(req)));
  const account = (await accountForUser(user.id));
  if (account) (await saveAccount({ ...account, welcomed: true }));
  return json({ user: await currentUser() });
});
