import { createClient, type SupabaseClient } from '@supabase/supabase-js';

let client: SupabaseClient | undefined;
export function hostedStorage() {
  const mode = process.env.GARRA_STORAGE || (process.env.VERCEL ? 'supabase' : 'sqlite');
  if (!['sqlite', 'supabase'].includes(mode)) throw new Error('GARRA_STORAGE must be sqlite or supabase.');
  if (process.env.VERCEL && mode !== 'supabase') throw new Error('Vercel requires durable Supabase storage.');
  return mode === 'supabase';
}
export function admin() {
  if (typeof window !== 'undefined') throw new Error('The database administrator client is server-only.');
  if (client) return client;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SECRET_KEY;
  if (!url || !key) throw new Error('Configure the Supabase URL and server-only SUPABASE_SECRET_KEY.');
  client = createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } });
  return client;
}
export async function data<T>(request: PromiseLike<{data:T|null;error:{code?:string;message:string}|null}>): Promise<T> {
  const result = await request;
  if (result.error) throw new Error('Database operation failed. Please try again.');
  return result.data as T;
}
export async function rpc<T>(name: string, args?: Record<string, unknown>): Promise<T> {
  return data(admin().rpc(name, args)) as Promise<T>;
}
