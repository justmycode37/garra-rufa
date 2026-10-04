import { z } from 'zod';
import { safeSourceUrl } from './source-schema';

const publicUrl = z.string().max(2000).refine(value => /^https?:\/\//.test(value) && !!safeSourceUrl(value));
const optionalText = z.string().max(1000).nullish().transform(value => value || undefined).optional();
export const atlasNodeSchema = z.object({
  id: z.string().min(1).max(300), label: z.string().min(1).max(2000), kind: z.string().max(100),
  description: z.string().max(10000).default(''), url: publicUrl.nullish().transform(value => value || undefined).optional(),
  providers: z.array(z.string().max(100)).max(30).default([]), email: optionalText, phone: optionalText, location: optionalText,
  xrefs: z.array(z.string().max(300)).max(1000).default([]),
});
export const atlasEdgeSchema = z.object({
  from: z.string().max(300), to: z.string().max(300), relation: z.string().max(200),
  source: z.string().max(100), evidence: z.array(z.string().max(2000)).max(500).default([]),
});
export const repositoryGraphSchema = z.object({ nodes: z.array(atlasNodeSchema).max(10000), edges: z.array(atlasEdgeSchema).max(30000), focus: z.array(z.string()).default([]) });
export const atlasGraphSchema = repositoryGraphSchema.omit({ focus: true }).extend({
  status: z.enum(['ok', 'empty', 'partial']), root: z.string(), total: z.number().int().nonnegative(), offset: z.number().int().nonnegative(),
  nextOffset: z.number().int().nonnegative().nullable(), datasetVersion: z.string(), retrievedAt: z.string(),
  unavailableProviders: z.array(z.string()), notice: z.string(),
});
export type AtlasNode = z.infer<typeof atlasNodeSchema>;
export type AtlasEdge = z.infer<typeof atlasEdgeSchema>;
export type AtlasGraphData = z.infer<typeof atlasGraphSchema>;
export type RepositoryGraph = z.infer<typeof repositoryGraphSchema>;
export const ATLAS_GRAPH_ZOOM = 3;

// HPO terms verified against the repository's hp.obo. Substructures keep their
// own association set instead of inheriting a nearby organ's demo conditions.
export const atlasRegions: Record<string, { label: string; hpo: string }> = {
  brain: { label: 'Brain & nerves', hpo: 'HP:0000707' }, heart: { label: 'Heart', hpo: 'HP:0001627' },
  eyes: { label: 'Eyes', hpo: 'HP:0000478' }, hands: { label: 'Hands & joints', hpo: 'HP:0001155' },
  legs: { label: 'Legs & feet', hpo: 'HP:0002814' }, muscles: { label: 'Muscles', hpo: 'HP:0003011' },
  skeleton: { label: 'Skeleton', hpo: 'HP:0000924' }, kidneys: { label: 'Kidneys', hpo: 'HP:0000077' },
  lungs: { label: 'Lungs & breathing', hpo: 'HP:0002086' }, liver: { label: 'Liver', hpo: 'HP:0001392' },
  aorta: { label: 'Aorta', hpo: 'HP:0001679' }, coronary: { label: 'Coronary vessels', hpo: 'HP:0006704' },
  stomach: { label: 'Stomach', hpo: 'HP:0002577' }, intestines: { label: 'Intestines', hpo: 'HP:0002242' },
  pancreas: { label: 'Pancreas', hpo: 'HP:0001732' }, spleen: { label: 'Spleen', hpo: 'HP:0001743' },
  gallbladder: { label: 'Gallbladder', hpo: 'HP:0005264' }, bladder: { label: 'Bladder', hpo: 'HP:0000014' },
  ureters: { label: 'Ureters', hpo: 'HP:0000069' }, adrenals: { label: 'Adrenal glands', hpo: 'HP:0000834' },
  pituitary: { label: 'Pituitary', hpo: 'HP:0000864' }, esophagus: { label: 'Esophagus', hpo: 'HP:0002031' },
  'salivary-glands': { label: 'Salivary glands', hpo: 'HP:0010286' }, rectum: { label: 'Rectum', hpo: 'HP:0002034' },
  tongue: { label: 'Tongue', hpo: 'HP:0000157' }, diaphragm: { label: 'Diaphragm', hpo: 'HP:0000775' },
  spine: { label: 'Spine', hpo: 'HP:0000925' }, skull: { label: 'Skull', hpo: 'HP:0000929' },
  teeth: { label: 'Teeth', hpo: 'HP:0000164' }, 'rib-cage': { label: 'Rib cage', hpo: 'HP:0001547' },
  pelvis: { label: 'Pelvis', hpo: 'HP:0040163' }, 'arm-bones': { label: 'Shoulders & arms', hpo: 'HP:0002817' },
  'hand-bones': { label: 'Hand bones', hpo: 'HP:0001155' }, 'leg-bones': { label: 'Leg bones', hpo: 'HP:0002814' },
  'foot-bones': { label: 'Feet', hpo: 'HP:0001760' }, iris: { label: 'Iris', hpo: 'HP:0000478' },
  cornea: { label: 'Cornea', hpo: 'HP:0000481' }, lenses: { label: 'Lens', hpo: 'HP:0000517' },
  'optic-nerves': { label: 'Optic nerves', hpo: 'HP:0000587' }, airways: { label: 'Airways', hpo: 'HP:0002086' },
};

export function atlasNodeKind(node: AtlasNode) {
  if (['doctor', 'clinician', 'investigator'].includes(node.kind)) return 'doctor';
  if (['expert_centre', 'healthcare_provider', 'hospital'].includes(node.kind)) return 'centre';
  if (['clinical_trial', 'trial'].includes(node.kind)) return 'trial';
  if (['disease', 'gene', 'phenotype', 'paper'].includes(node.kind)) return node.kind;
  return 'resource';
}

/** Merge by canonical identifiers; only retain relationships with both endpoints. */
export function mergeAtlasGraphs(base: AtlasGraphData, additions: RepositoryGraph[]): AtlasGraphData {
  const nodes = new Map(base.nodes.map(node => [node.id, node]));
  const edges = new Map<string, AtlasEdge>();
  for (const graph of additions) for (const node of graph.nodes) {
    const prior = nodes.get(node.id);
    nodes.set(node.id, { ...prior, ...node, description: node.description || prior?.description || '', url: node.url || prior?.url,
      providers: [...new Set([...(prior?.providers ?? []), ...node.providers])] });
  }
  for (const edge of [...base.edges, ...additions.flatMap(graph => graph.edges)]) {
    if (!nodes.has(edge.from) || !nodes.has(edge.to)) continue;
    const key = JSON.stringify([edge.from, edge.to, edge.relation, edge.source]);
    const previous = edges.get(key);
    edges.set(key, { ...edge, evidence: [...new Set([...(previous?.evidence ?? []), ...edge.evidence])] });
  }
  return { ...base, nodes: [...nodes.values()], edges: [...edges.values()] };
}

export function connectedAtlasNodes(graph: Pick<AtlasGraphData, 'nodes' | 'edges'>, root: string) {
  const reachable = new Set([root]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const edge of graph.edges) {
      if (reachable.has(edge.from) !== reachable.has(edge.to)) {
        reachable.add(edge.from); reachable.add(edge.to); changed = true;
      }
    }
  }
  return { nodes: graph.nodes.filter(node => reachable.has(node.id)), edges: graph.edges.filter(edge => reachable.has(edge.from) && reachable.has(edge.to)) };
}

/** Shortest evidenced paths, also used to explain collapsed graph connections. */
export function atlasPaths(graph: Pick<AtlasGraphData, 'nodes' | 'edges'>, root: string) {
  const adjacent = new Map<string, { id: string; edge: AtlasEdge }[]>();
  for (const edge of graph.edges) {
    for (const [from, to] of [[edge.from, edge.to], [edge.to, edge.from]]) {
      const entries = adjacent.get(from) ?? []; entries.push({ id: to, edge }); adjacent.set(from, entries);
    }
  }
  const paths = new Map<string, AtlasEdge[]>([[root, []]]), queue = [root];
  for (let index = 0; index < queue.length; index++) for (const next of adjacent.get(queue[index]) ?? []) {
    if (paths.has(next.id)) continue;
    paths.set(next.id, [...paths.get(queue[index])!, next.edge]); queue.push(next.id);
  }
  return paths;
}
