import { cookies } from 'next/headers';
import { createSession, sessionUser, removeSession } from './persistence';
import { createSupabaseServerClient } from './supabase/server';
import { authWorkspaceUser } from './auth-user';
import { getSupabaseConfig } from './supabase/config';
import type { User as AuthUser } from '@supabase/supabase-js';
import type { Role } from './types';

export function authOrigin(requestUrl: string) {
  const origin = new URL(process.env.APP_ORIGIN || requestUrl);
  if (origin.username || origin.password || (origin.protocol !== 'https:' && !(origin.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(origin.hostname)))) throw new Error('Sign-in requires HTTPS.');
  return origin.origin;
}
export async function workspaceForIdentity(user: AuthUser, role?: Role) {
  if (!user.email || !user.email_confirmed_at || user.is_anonymous) return null;
  // Metadata supplies display text only, never permissions or a role.
  const name = String(user.user_metadata?.full_name || user.user_metadata?.name || user.email.split('@')[0]).trim().slice(0, 100) || 'Member';
  return authWorkspaceUser({ id: user.id, email: user.email, name }, role);
}
export async function establishSession(identity: AuthUser, role?: Role) {
  const user = await workspaceForIdentity(identity, role);
  if (!user) return null;
  const jar = await cookies();
  await removeSession(jar.get('garra_session')?.value || '');
  const token = await createSession(user.id);
  jar.set('garra_session', token, { httpOnly: true, secure: process.env.NODE_ENV === 'production', sameSite: 'lax', path: '/', maxAge: 7 * 86400 });
  return user;
}
export async function authenticatedUser() {
  const token = (await cookies()).get('garra_session')?.value;
  if (!token) return null;
  const session = await sessionUser(token);
  if (!session || session.guest) return null;
  const supabase = await createSupabaseServerClient();
  // Require both a remotely verified Auth identity and a revocable app session.
  // Signing out invalidates access immediately even while an Auth JWT is valid.
  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user || data.user.is_anonymous || !data.user.email_confirmed_at || data.user.id !== session.id) return null;
  return workspaceForIdentity(data.user);
}
let settings: { expires: number; google: boolean } | undefined;
export async function googleEnabled() {
  if (settings && settings.expires > Date.now()) return settings.google;
  const { url, publishableKey } = getSupabaseConfig();
  try {
    const response = await fetch(`${url}/auth/v1/settings`, { headers: { apikey: publishableKey }, cache: 'no-store', signal: AbortSignal.timeout(5000) });
    if (!response.ok) return false;
    const value = await response.json();
    const google = value.external?.google === true;
    settings = { google, expires: Date.now() + 30000 };
    return google;
  } catch { return false; }
}
