import 'server-only';
import { createServerClient } from '@supabase/ssr';
import { cookies } from 'next/headers';
import { getSupabaseConfig } from './config';

// Used only in Route Handlers, where session refreshes can update cookies.
// A new client per request prevents one visitor's session leaking to another.
export async function createSupabaseServerClient() {
  const { url, publishableKey } = getSupabaseConfig();
  const jar = await cookies();
  return createServerClient(url, publishableKey, {
    cookieOptions: { httpOnly: true, sameSite: 'lax', secure: process.env.NODE_ENV === 'production', path: '/' },
    cookies: {
      getAll: () => jar.getAll(),
      setAll: values => values.forEach(({ name, value, options }) => jar.set(name, value, options)),
    },
  });
}
