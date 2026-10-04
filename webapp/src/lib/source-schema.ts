import { z } from 'zod';

export function safeSourceUrl(value: string) {
  if (/^\/(?!\/)/.test(value) && !value.includes('\\')) return value;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : undefined; }
  catch { return undefined; }
}
const optionalText = (max: number) => z.string().max(max).nullish().transform(value => value || undefined);
const optionalBoolean = z.boolean().nullish().transform(value => value ?? undefined);
export const sourceSchema = z.object({
  id: z.string().min(1).max(160), title: z.string().min(1).max(2000),
  url: z.string().max(2000).refine(value => !!safeSourceUrl(value), 'Invalid source URL'),
  kind: z.enum(['reference', 'paper', 'workspace', 'contact', 'organization', 'trial']),
  excerpt: z.string().max(10000), year: optionalText(20), journal: optionalText(300),
  authors: z.array(z.string().max(300)).max(30).optional(), doi: optionalText(300), pmid: optionalText(30),
  providers: z.array(z.string().max(100)).max(30).optional(), retrievedAt: optionalText(60),
  openAccess: optionalBoolean, preprint: optionalBoolean,
  entityId: optionalText(300), recordType: optionalText(100),
  relations: z.array(z.string().max(2000)).max(20).optional(),
  email: optionalText(254), phone: optionalText(100), location: optionalText(300),
});
