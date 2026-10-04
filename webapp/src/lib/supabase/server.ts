import 'server-only';

import { createClient } from '@supabase/supabase-js';
import { getSupabaseConfig } from './config';

// This client uses the public database role. The app's local workspace session
// is not a Supabase Auth session and must never be treated as one for RLS.
export function createSupabaseServerClient() {
  const { url, publishableKey } = getSupabaseConfig();
  return createClient(url, publishableKey, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });
}
