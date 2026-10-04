import { handler, HttpError } from '@/lib/http';
const retired = handler(async () => { throw new HttpError(410, 'ChatGPT sign-in has been replaced by email/password and Google sign-in.'); });
export const GET = retired;
export const POST = retired;
export const PATCH = retired;
