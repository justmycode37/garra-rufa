import { existsSync } from 'node:fs';
import { loadEnvFile } from 'node:process';
import { getSupabaseConfig } from '../src/lib/supabase/config';

if (existsSync('.env.local')) loadEnvFile('.env.local');
else if (existsSync('.env')) loadEnvFile('.env');

async function checkConnection() {
  const { url, publishableKey } = getSupabaseConfig();
  const headers = { apikey: publishableKey };
  // Supabase's OpenAPI root requires a secret key. A deliberately absent table
  // instead verifies that our publishable key reaches PostgREST's schema cache.
  // This also works before the project has its first application table.
  for (const [service, path] of [['Database API', '/rest/v1/__garra_connection_probe__?select=*&limit=0'], ['Auth API', '/auth/v1/settings']]) {
    const response = await fetch(new URL(path, url), { headers, signal: AbortSignal.timeout(15_000) });
    const body = await response.json();
    const emptySchema = service === 'Database API' && response.status === 404 && body.code === 'PGRST205';
    if (!response.ok && !emptySchema) throw new Error(`${service} connection failed (HTTP ${response.status}).`);
    console.log(`${service}: connected`);
  }
  console.log(`Project: ${new URL(url).hostname.split('.')[0]}`);
}

checkConnection().catch(error => {
  console.error(error instanceof Error ? error.message : 'Supabase connection failed.');
  process.exitCode = 1;
});
