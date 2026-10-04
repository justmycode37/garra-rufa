import test from 'node:test';
import assert from 'node:assert/strict';
import { atlasGraphSchema, atlasNodeSchema, atlasNodeKind, atlasPaths, mergeAtlasGraphs, connectedAtlasNodes } from '../src/lib/atlas-graph';
import { expandAtlasDisease, loadAtlasRegion } from '../src/lib/atlas-repository';

const node = (id: string, kind = 'phenotype') => atlasNodeSchema.parse({ id, label: id, kind, providers: ['HPO'] });
const edge = (from: string, to: string) => ({ from, to, relation: 'has_phenotype', source: 'HPO', evidence: ['PMID:1'] });
const base = () => atlasGraphSchema.parse({ status: 'ok', root: 'HP:1', nodes: [node('HP:1'), node('HP:2'), node('OMIM:1', 'disease')],
  edges: [edge('HP:1', 'HP:2'), edge('OMIM:1', 'HP:2')], total: 1, offset: 0, nextOffset: null, datasetVersion: '2026-09-02', retrievedAt: '2026-10-04', unavailableProviders: [], notice: '' });

test('regional paths expose actual intermediate phenotypes and do not invent direct links', () => {
  const paths = atlasPaths(base(), 'HP:1');
  assert.equal(paths.get('OMIM:1')?.length, 2);
  assert.deepEqual(paths.get('OMIM:1')?.map(e => e.to), ['HP:2', 'HP:2']);
  assert.equal(paths.get('unrelated'), undefined);
});

test('merge deduplicates identifiers and evidence and excludes dangling endpoints', () => {
  const data = base();
  const result = mergeAtlasGraphs(data, [{ nodes: [node('OMIM:1', 'disease'), node('HGNC:1', 'gene')], focus: [], edges: [edge('OMIM:1', 'HGNC:1'), { ...data.edges[0], evidence: ['PMID:2'] }, edge('missing', 'HGNC:1')] }]);
  assert.equal(result.nodes.length, 4);
  assert.equal(result.edges.length, 3);
  assert.deepEqual(result.edges[0].evidence, ['PMID:1', 'PMID:2']);
});

test('unconnected matches stay outside the regional graph and centres are never called doctors', () => {
  const result = connectedAtlasNodes({ nodes: [...base().nodes, node('unrelated')], edges: base().edges }, 'HP:1');
  assert.equal(result.nodes.length, 3);
  assert.equal(atlasNodeKind(node('centre', 'expert_centre')), 'centre');
  assert.equal(atlasNodeKind(node('person', 'clinician')), 'doctor');
  assert.equal(atlasNodeKind(node('trial', 'clinical_trial')), 'trial');
});

test('regional endpoint sends only bounded public anatomy queries and rejects invalid source URLs', async t => {
  t.mock.method(globalThis, 'fetch', async (url: URL, options: RequestInit) => {
    assert.equal(url.pathname, '/api/research/atlas');
    assert.deepEqual(JSON.parse(String(options.body)), { region: 'HP:0001626', offset: 6, query: 'Marfan', limit: 6 });
    return Response.json(base());
  });
  assert.equal((await loadAtlasRegion('HP:0001626', 6, 'Marfan')).total, 1);
  for (const url of ['javascript:alert(1)', '/api/files/private', 'https://user:password@example.org']) assert.throws(() => atlasNodeSchema.parse({ ...node('test'), url }));
});

test('expansion keeps partial results, preserves identifier equivalence and excludes unrelated matches', async t => {
  t.mock.method(globalThis, 'fetch', async (url: URL, options: RequestInit) => {
    const request = JSON.parse(String(options.body));
    if (request.category === 'contacts') return new Response('', { status: 503 });
    const papers = url.pathname.endsWith('/papers');
    return Response.json({ status: 'ok', query: request.query, sources: [], pipeline: papers ? 'repository-literature' : 'repository-graph', providers: ['monarch'], unavailableProviders: [], retrievedAt: '2026-10-04', cached: false,
      ...(!papers ? { graph: { nodes: [{ ...node('MONDO:1', 'disease'), xrefs: ['OMIM:1'] }, node('HGNC:1', 'gene'), node('MONDO:99', 'disease'), node('parent', 'disease'), node('unrelated-trial', 'clinical_trial')], edges: [edge('MONDO:1', 'HGNC:1'), { ...edge('MONDO:1', 'parent'), relation: 'subclass_of' }, edge('parent', 'unrelated-trial')], focus: ['MONDO:1'] } } : {}) });
  });
  const result = await expandAtlasDisease('OMIM:1', 'Example condition');
  assert.deepEqual(result.unavailableProviders, ['specialist resources']);
  assert.deepEqual(new Set(result.graph.nodes.map(n => n.id)), new Set(['MONDO:1', 'HGNC:1', 'OMIM:1', 'parent']));
  assert.ok(result.graph.edges.some(e => e.relation === 'same_as'));
});
