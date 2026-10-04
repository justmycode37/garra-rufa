'use client';

import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import { getSupabaseConfig } from './config';

let client: SupabaseClient | undefined;

export function createSupabaseBrowserClient() {
  if (client) return client;
  const { url, publishableKey } = getSupabaseConfig();
  client = createClient(url, publishableKey);
  return client;
}
