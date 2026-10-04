import { z } from 'zod';

// Common findings per organ (public/organ-findings.json), exported from the HPO
// annotations by scripts/export_organ_findings.py; offered as terms to add.
export const organFindingsSchema = z.object({
  source: z.string(), exported: z.string(),
  regions: z.record(z.string(), z.object({
    hpo: z.string(), hpo_label: z.string(), diseases: z.number().int(),
    findings: z.array(z.object({ id: z.string().regex(/^HP:\d{7}$/), label: z.string(), diseases: z.number().int() })),
  })),
});
export type OrganFindings = z.infer<typeof organFindingsSchema>;

/** A term added to an organ query: free text, or an HPO finding picked from the list. */
export type OrganTerm = { text: string; id?: string };

// The research service reads the query as comma-separated symptoms, HP ids and genes
// (src/query-test/resolve.py) and accepts at most 120 characters of \w and ,.;:'()+/-
export const MAX_QUERY = 120;
const TERM = /^[\p{L}\p{N}_][\p{L}\p{N}_\s.:'()+/-]*$/u;

/** A cleaned term, or why it cannot be used. */
export function readTerm(raw: string): { term?: string; error?: string } {
  const term = raw.replace(/\s+/g, ' ').trim();
  if (!term) return {};
  if (term.length < 2) return { error: 'Use at least two characters per term.' };
  if (term.length > 60) return { error: 'Keep each term under 60 characters.' };
  if (!TERM.test(term) || term.includes('://')) return { error: 'Use letters, numbers and simple punctuation only (no commas inside a term).' };
  return { term };
}

/** The build request for an organ and the added terms: the organ's HPO term comes first. */
export function organQuery(organ: { label: string; hpo: string }, terms: OrganTerm[]) {
  const unique = terms.filter((term, index) => terms.findIndex(other => other.text.toLowerCase() === term.text.toLowerCase()) === index);
  if (!unique.length) return { error: 'Add at least one symptom, gene or finding.' };
  const query = [organ.hpo, ...unique.map(term => term.id ?? term.text)].join(', ');
  if (query.length > MAX_QUERY) return { error: 'Too many terms for one graph. Remove one or use shorter terms.' };
  return { query, label: `${organ.label}: ${unique.map(term => term.text).join(', ')}`.slice(0, 200) };
}
