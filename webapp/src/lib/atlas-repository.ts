import { atlasGraphSchema, connectedAtlasNodes, type AtlasNode, type AtlasEdge, type RepositoryGraph } from './atlas-graph';
import { searchResearch, ResearchUnavailable } from './research';

export async function loadAtlasRegion(hpo: string, offset: number, query: string, signal?: AbortSignal) {
  try {
    const endpoint = new URL('/api/research/atlas', process.env.GARRA_RESEARCH_URL || 'http://127.0.0.1:8787');
    if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.username || endpoint.password) throw new Error('Invalid service');
    const response = await fetch(endpoint, { method: 'POST', redirect: 'error', cache: 'no-store', headers: { 'Content-Type': 'application/json', ...(process.env.GARRA_RESEARCH_TOKEN ? { Authorization: `Bearer ${process.env.GARRA_RESEARCH_TOKEN}` } : {}) },
      body: JSON.stringify({ region: hpo, offset, query, limit: 6 }), signal: AbortSignal.any([AbortSignal.timeout(20000), ...(signal ? [signal] : [])]) });
    if (!response.ok) throw new Error('Atlas unavailable');
    return atlasGraphSchema.parse(await response.json());
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ResearchUnavailable('Regional data is unavailable. Start the repository backend with its HPO datasets and retry.');
  }
}

export async function expandAtlasDisease(entity: string, label: string, signal?: AbortSignal) {
  const requests = await Promise.allSettled([
    searchResearch(entity, { limit: 20, signal }),
    searchResearch(entity, { contacts: true, limit: 20, signal }),
    searchResearch(label, { papers: true, limit: 8, signal }),
  ]);
  if (signal?.aborted) throw signal.reason;
  const nodes = new Map<string, AtlasNode>();
  const edges: AtlasEdge[] = [];
  const unavailable = new Set<string>();
  for (const [index, result] of requests.entries()) {
    if (result.status === 'rejected') { unavailable.add(['disease graph', 'specialist resources', 'literature'][index]); continue; }
    result.value.unavailableProviders.forEach(provider => unavailable.add(provider));
    const graph = result.value.graph;
    if (graph) {
      const focus = new Set(graph.nodes.filter(node => node.id === entity || node.xrefs.includes(entity)).map(node => node.id));
      const adjacent = new Set(focus);
      const keptEdges = graph.edges.filter(edge => focus.has(edge.from) || focus.has(edge.to));
      keptEdges.forEach(edge => { adjacent.add(edge.from); adjacent.add(edge.to); });
      // Follow centres/networks/trials to their people or affiliations, but
      // never inherit other diseases' genes or trials through a broad parent.
      const resourceKinds = new Set(['expert_centre', 'healthcare_provider', 'hospital', 'organisation', 'organization', 'expert_network', 'research_network', 'network', 'clinical_trial']);
      const resources = new Set(graph.nodes.filter(node => adjacent.has(node.id) && resourceKinds.has(node.kind)).map(node => node.id));
      const resourceEdges = graph.edges.filter(edge => resources.has(edge.from) && !focus.has(edge.to) && graph.nodes.find(node => node.id === edge.to)?.kind !== 'disease');
      resourceEdges.forEach(edge => adjacent.add(edge.to));
      for (const node of graph.nodes) {
        if (!adjacent.has(node.id)) continue;
        nodes.set(node.id, node);
        // Identifier equivalence is asserted only when supplied by the repository.
        if (node.id !== entity && node.xrefs.includes(entity)) edges.push({ from: entity, to: node.id, relation: 'same_as', source: node.providers.join(', ') || 'repository', evidence: [] });
      }
      edges.push(...keptEdges, ...resourceEdges);
    }
    if (index === 2) for (const paper of result.value.sources) {
      const id = paper.pmid ? `PMID:${paper.pmid}` : paper.id;
      nodes.set(id, { id, label: paper.title, kind: 'paper', description: paper.excerpt, url: paper.url, providers: paper.providers ?? [], xrefs: [] });
      edges.push({ from: entity, to: id, relation: 'literature_search_result', source: 'repository-literature', evidence: paper.pmid ? [`PMID:${paper.pmid}`] : [] });
    }
  }
  if (requests.every(result => result.status === 'rejected')) throw new ResearchUnavailable('Connected research providers could not be reached. The regional HPO graph is still available.');
  // The existing disease is the entry point. Disconnected search matches and
  // broader-condition resources do not acquire an invented regional association.
  const entry: AtlasNode = nodes.get(entity) ?? { id: entity, label, kind: 'disease', description: '', providers: [], xrefs: [] };
  nodes.set(entity, entry);
  const connected = connectedAtlasNodes({ nodes: [...nodes.values()], edges }, entity);
  const graph: RepositoryGraph = { ...connected, focus: [entity] };
  return { graph, unavailableProviders: [...unavailable] };
}
