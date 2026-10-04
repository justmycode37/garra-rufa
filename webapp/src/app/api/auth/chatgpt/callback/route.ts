import { NextResponse } from 'next/server';
import { authOrigin } from '@/lib/auth';
export async function GET(req: Request) {
  return NextResponse.redirect(`${authOrigin(req.url)}/?auth=changed`, { headers: { 'Cache-Control': 'no-store' } });
}
