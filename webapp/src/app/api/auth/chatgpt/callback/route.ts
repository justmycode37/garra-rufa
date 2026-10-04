import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import { consumeAttempt } from '@/lib/chatgpt-store';
import { chatGPTRequestUrl, finishChatGPTSignIn, localChatGPTOrigin } from '@/lib/chatgpt';
import { createSession, removeSession, sessionUser } from '@/lib/store';
import { saveLandingConversation } from '@/lib/chat-transfer';

export const runtime = 'nodejs';
export async function GET(req: Request) {
  const c = await cookies();
  let callback: URL;
  try { callback = new URL(chatGPTRequestUrl(req)); localChatGPTOrigin(callback.toString()); }
  catch { return new NextResponse('ChatGPT sign-in requires the local app.', { status: 400 }); }
  const destination = new URL('/', callback.origin);
  try {
    localChatGPTOrigin(callback.toString());
    const attempt = consumeAttempt(c.get('garra_chatgpt_attempt')?.value || '', callback.searchParams.get('state'));
    if (attempt.browserId !== c.get('garra_chatgpt_browser')?.value || attempt.currentUserId !== sessionUser(c.get('garra_session')?.value || '')?.id || new URL(attempt.redirectUri).origin !== callback.origin) {
      throw new Error('The workspace changed during sign-in. Please try again.');
    }
    const account = await finishChatGPTSignIn(attempt, callback);
    removeSession(c.get('garra_session')?.value || '');
    c.set('garra_session', createSession(account.userId), { httpOnly: true, sameSite: 'lax', path: '/', maxAge: 7 * 86400 });
    destination.searchParams.set('chatgpt', 'connected');
    if (attempt.conversation) destination.searchParams.set('chat', saveLandingConversation(account.userId, attempt.conversation).id);
  } catch {
    // Do not put provider error descriptions, codes, or tokens in redirect URLs or logs.
    destination.searchParams.set('chatgpt', 'error');
  } finally {
    c.set('garra_chatgpt_attempt', '', { httpOnly: true, sameSite: 'lax', path: '/api/auth/chatgpt', maxAge: 0 });
  }
  const response = NextResponse.redirect(destination, 303);
  response.headers.set('Cache-Control', 'no-store');
  response.headers.set('Referrer-Policy', 'no-referrer');
  return response;
}
