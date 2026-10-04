import { z } from 'zod';

// Per-organ disease lists (public/organ-diseases/<region>.json), exported from the HPO
// annotations by scripts/export_organ_diseases.py.
export const organDiseaseSchema = z.object({
  id: z.string().regex(/^(OMIM|ORPHA|DECIPHER):\d+$/), ids: z.array(z.string()).optional(), name: z.string(),
  score: z.number(), features: z.number().int(), frequency: z.string(),
  top: z.array(z.string()), genes: z.array(z.string()),
});
export const organDiseaseListSchema = z.object({
  region: z.string(), label: z.string(), hpo: z.string(), hpo_label: z.string(), scope: z.string().nullish(),
  source: z.string(), exported: z.string(), total: z.number().int(), diseases: z.array(organDiseaseSchema),
});
export type OrganDisease = z.infer<typeof organDiseaseSchema>;
export type OrganDiseaseList = z.infer<typeof organDiseaseListSchema>;

/** The graph page for a disease picked in the atlas. */
export function diseaseGraphHref(disease: Pick<OrganDisease, 'id' | 'ids' | 'name'>) {
  const ids = [disease.id, ...(disease.ids ?? []).filter(id => id !== disease.id)];
  return `/graphs/overview?${new URLSearchParams({ disease: ids.join(','), name: disease.name })}`;
}

export type DiseaseRequest = { ids: string[]; name: string };
/** Reads ?disease=OMIM:154700,ORPHA:558&name=… (invalid parts are dropped). */
export function diseaseRequest(params: URLSearchParams): DiseaseRequest | undefined {
  const ids = (params.get('disease') ?? '').split(',').map(id => id.trim()).filter(id => /^(OMIM|ORPHA|DECIPHER|MONDO):\d+$/.test(id)).slice(0, 6);
  const name = (params.get('name') ?? '').replace(/[\x00-\x1f]/g, ' ').trim().slice(0, 200);
  return ids.length ? { ids, name: name || ids[0] } : undefined;
}

const normalize = (text: string) => text.toLowerCase().replace(/[’']/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
type Candidate = { id: string; label: string; ids?: string[]; names?: string[]; query?: string };
/** A graph already made for the disease: same identifier, or the same name / synonym. */
export function findDiseaseRun<T extends Candidate>(runs: T[], request: DiseaseRequest): T | undefined {
  const ids = new Set(request.ids);
  const name = normalize(request.name);
  return runs.find(run => run.ids?.some(id => ids.has(id)) || (run.query !== undefined && ids.has(run.query)))
    ?? runs.find(run => [run.label, ...(run.names ?? [])].some(label => normalize(label) === name));
}
