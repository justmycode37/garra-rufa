import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import { authOrigin, establishSession } from '@/lib/auth';
import { createSupabaseServerClient } from '@/lib/supabase/server';
export async function GET(req: Request) {
  const url = new URL(req.url), origin = authOrigin(req.url);
  const redirect = (state: string) => NextResponse.redirect(`${origin}/?auth=${state}`, { headers: { 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' } });
  try {
    const code = url.searchParams.get('code');
    if (!code || url.searchParams.has('error')) return redirect('error');
    const supabase = await createSupabaseServerClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) return redirect('error');
    const { data, error: invalid } = await supabase.auth.getUser();
    if (invalid || !data.user) return redirect('error');
    const jar = await cookies();
    const selected = jar.get('garra_auth_role')?.value;
    const role = selected === 'researcher' || selected === 'doctor' || selected === 'patient' ? selected : undefined;
    if (!await establishSession(data.user, role)) return redirect('error');
    jar.delete('garra_auth_role');
    return redirect(url.searchParams.get('recovery') === '1' ? 'recovery' : 'connected');
  } catch { return redirect('error'); }
}
