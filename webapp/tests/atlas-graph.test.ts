import test from 'node:test';
import assert from 'node:assert/strict';
import { atlasGraphSchema, atlasNodeSchema, atlasNodeKind, atlasPaths, mergeAtlasGraphs, connectedAtlasNodes } from '../src/lib/atlas-graph';
import { expandAtlasDisease, loadAtlasRegion, loadAtlasEntity } from '../src/lib/atlas-repository';

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

test('expansion keeps cross references without asserting equivalence or inheriting subtype associations', async t => {
  t.mock.method(globalThis, 'fetch', async (url: URL, options: RequestInit) => {
    const request = JSON.parse(String(options.body));
    if (request.category === 'contacts') return new Response('', { status: 503 });
    const papers = url.pathname.endsWith('/papers');
    return Response.json({ status: 'ok', query: request.query, sources: [], pipeline: papers ? 'repository-literature' : 'repository-graph', providers: ['monarch'], unavailableProviders: [], retrievedAt: '2026-10-04', cached: false,
      ...(!papers ? { graph: { nodes: [{ ...node('MONDO:1', 'disease'), xrefs: ['OMIM:1'] }, node('HGNC:1', 'gene'), node('MONDO:99', 'disease'), node('parent', 'disease'), node('unrelated-trial', 'clinical_trial')], edges: [edge('MONDO:1', 'HGNC:1'), { ...edge('MONDO:1', 'parent'), relation: 'subclass_of' }, edge('parent', 'unrelated-trial')], focus: ['MONDO:1'] } } : {}) });
  });
  const result = await expandAtlasDisease('OMIM:1', 'Example condition');
  assert.deepEqual(result.unavailableProviders, ['specialist resources']);
  assert.deepEqual(new Set(result.graph.nodes.map(n => n.id)), new Set(['MONDO:1', 'OMIM:1']));
  assert.ok(result.graph.edges.some(e => e.relation === 'cross_reference'));
  assert.ok(!result.graph.edges.some(e => e.relation === 'same_as'));
});


test('claim metadata survives schema parsing and graph merging without becoming a reusable asset', () => {
  const claim = atlasNodeSchema.parse({id:'evidence:c1',label:'A claim',kind:'claim',
    url:'https://pmc.ncbi.nlm.nih.gov/articles/PMC123/',access:'Open full text in PMC',
    claim:{id:'c1',subject:'OMIM:1',object:'HP:2',relationship:'has_phenotype',paper_id:'PMID:1',
      direction:'unknown',polarity:'asserted',reviewed:false,passage:'Exact passage',context:{model:'human'},
      limitations:['Review only'],study_type:'review',locator:{section:'Results'}}});
  const paper = node('PMID:1','paper');
  const graph = mergeAtlasGraphs(base(), [{nodes:[claim,paper],edges:[edge('OMIM:1','evidence:c1'),edge('PMID:1','evidence:c1')],focus:[]}]);
  assert.deepEqual(graph.nodes.find(n=>n.id==='evidence:c1')?.claim,claim.claim);
  assert.equal(atlasNodeKind(claim),'claim');
  assert.equal(atlasNodeKind(node('GO:1','process')),'pathway');
  assert.equal(atlasNodeKind(paper),'paper');
  assert.equal(atlasPaths(graph,'OMIM:1').get('PMID:1')?.length,2);
});


test('saved graph stays available when explicit external enrichment fails', async t => {
  let lookups=0;
  t.mock.method(globalThis,'fetch',async (url:URL) => {
    if(url.pathname==='/api/entity') return Response.json({graph:{nodes:[node('OMIM:1','disease'),node('PMID:1','paper')],edges:[edge('OMIM:1','PMID:1')],focus:['OMIM:1']}});
    lookups++;return new Response('',{status:503});
  });
  const signal=new AbortController().signal;
  const saved=await loadAtlasEntity('OMIM:1','Example',false,signal);
  assert.equal(lookups,0);assert.equal(saved.graph.nodes.length,2);
  const enriched=await loadAtlasEntity('OMIM:1','Example',true,signal);
  assert.equal(lookups,3);assert.deepEqual(enriched.graph,saved.graph);
  assert.ok(enriched.unavailableProviders.includes('live papers and resources'));
});

test('explicit enrichment adds literature results without manufacturing claims', async t => {
  t.mock.method(globalThis,'fetch',async (url:URL, options:RequestInit) => {
    if(url.pathname==='/api/entity') return Response.json({graph:{nodes:[node('OMIM:1','disease')],edges:[],focus:['OMIM:1']}});
    const papers=url.pathname.endsWith('/papers');
    const req=JSON.parse(String(options.body));
    return Response.json({status:'ok',query:req.query,pipeline:papers?'repository-literature':'repository-graph',providers:[],unavailableProviders:[],retrievedAt:'2026-10-04',cached:false,
      sources:papers?[{id:'PMID:12',pmid:'12',title:'A retrieved publication',url:'https://pubmed.ncbi.nlm.nih.gov/12/',kind:'paper',excerpt:'Abstract excerpt'}]:[]});
  });
  const result=await loadAtlasEntity('OMIM:1','Example',true,new AbortController().signal);
  assert.ok(result.graph.nodes.some(n=>n.id==='PMID:12'&&n.kind==='paper'));
  assert.ok(result.graph.edges.some(e=>e.relation==='literature_search_result'));
  assert.ok(!result.graph.nodes.some(n=>n.kind==='claim'));
});
