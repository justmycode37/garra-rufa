import { createHash } from 'node:crypto';
import { cookies } from 'next/headers';
import { z } from 'zod';
import { handler, json, currentUser, HttpError, body } from '@/lib/http';
import { limit, removeSession } from '@/lib/persistence';
import { authOrigin, googleEnabled, establishSession } from '@/lib/auth';
import { createSupabaseServerClient } from '@/lib/supabase/server';

export const GET = handler(async () => json({ user: await currentUser(), googleEnabled: await googleEnabled() }));
const inputSchema = z.discriminatedUnion('action', [
  z.object({ action: z.literal('login'), email: z.email().max(254), password: z.string().min(1).max(128), role: z.enum(['researcher', 'doctor', 'patient']).optional() }),
  z.object({ action: z.literal('signup'), email: z.email().max(254), password: z.string().min(10).max(128), name: z.string().trim().min(1).max(100), role: z.enum(['researcher', 'doctor', 'patient']) }),
  z.object({ action: z.literal('google'), role: z.enum(['researcher', 'doctor', 'patient']) }),
  z.object({ action: z.literal('reset'), email: z.email().max(254) }),
  z.object({ action: z.literal('password'), password: z.string().min(10).max(128) }),
]);
export const POST = handler(async req => {
  const input = inputSchema.parse(await body(req));
  if (!await limit('auth:all', 150, 10 * 60000)) throw new HttpError(429, 'Sign-in is busy. Please try again shortly.');
  if ('email' in input && !await limit(`auth:email:${createHash('sha256').update(input.email.toLowerCase()).digest('hex')}`, 12, 10 * 60000)) throw new HttpError(429, 'Too many attempts. Please try again in a few minutes.');
  const supabase = await createSupabaseServerClient();
  const callback = `${authOrigin(req.url)}/api/auth/callback`;
  const jar = await cookies();
  if (input.action === 'google') {
    if (!await googleEnabled()) throw new HttpError(503, 'Google sign-in is not available yet. Please use email and password.');
    const { data, error } = await supabase.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: callback, skipBrowserRedirect: true, scopes: 'openid email profile' } });
    if (error || !data.url) throw new HttpError(503, 'Google sign-in could not start. Please try again.');
    jar.set('garra_auth_role', input.role, { httpOnly: true, secure: process.env.NODE_ENV === 'production', sameSite: 'lax', path: '/', maxAge: 3600 });
    return json({ url: data.url });
  }
  if (input.action === 'reset') {
    const { error } = await supabase.auth.resetPasswordForEmail(input.email, { redirectTo: `${callback}?recovery=1` });
    if (error && error.status !== 429) throw new HttpError(503, 'Password reset email could not be sent. Please try again later.');
    return json({ message: 'If an account uses this email, a reset link will arrive shortly. Open it in this browser.' });
  }
  if (input.action === 'password') {
    const { data: verified, error: invalid } = await supabase.auth.getUser();
    if (invalid || !verified.user) throw new HttpError(401, 'Open the reset link from your email first.');
    const { error } = await supabase.auth.updateUser({ password: input.password });
    if (error) throw new HttpError(400, 'Choose a new password of at least 10 characters and try again.');
    return json({ user: await establishSession(verified.user), message: 'Password updated.' });
  }
  if (input.action === 'signup') {
    const { data, error } = await supabase.auth.signUp({ email: input.email, password: input.password, options: { emailRedirectTo: callback, data: { full_name: input.name } } });
    if (error) throw new HttpError(error.status === 429 ? 429 : 400, error.status === 429 ? 'Please wait before requesting another email.' : 'Account creation could not finish. Try signing in, or check your email and password.');
    if (data.session && data.user) return json({ user: await establishSession(data.user, input.role) });
    jar.set('garra_auth_role', input.role, { httpOnly: true, secure: process.env.NODE_ENV === 'production', sameSite: 'lax', path: '/', maxAge: 3600 });
    return json({ message: 'Check your email to confirm your account. Open the link in this browser, then sign in.' });
  }
  const { data, error } = await supabase.auth.signInWithPassword({ email: input.email, password: input.password });
  if (error || !data.user) throw new HttpError(401, 'Email or password is incorrect, or your email still needs confirmation.');
  const user = await establishSession(data.user, input.role);
  if (!user) throw new HttpError(401, 'Confirm your email before signing in.');
  jar.delete('garra_auth_role');
  return json({ user });
});
export const DELETE = handler(async () => {
  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.auth.signOut({ scope: 'local' });
  const jar = await cookies();
  await removeSession(jar.get('garra_session')?.value || '');
  for (const { name } of jar.getAll()) if (name.startsWith('sb-') || name.startsWith('garra_auth_') || name.startsWith('garra_chatgpt_') || name === 'garra_session') jar.delete(name);
  return json({ ok: true, warning: error ? 'Signed out on this device. The account service could not confirm session revocation.' : undefined });
});
