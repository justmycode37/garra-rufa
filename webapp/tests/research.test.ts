import test from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { searchResearch, researchWarning } from '../src/lib/research';
import { answerWithSources, remapCitations } from '../src/lib/answer-sources';
import { sourceSchema } from '../src/lib/source-schema';
import SourceCard from '../src/components/SourceCard';
import type { Source } from '../src/lib/types';

const paper: Source = { id:'paper-1', kind:'paper', title:'Verified paper', url:'https://pubmed.ncbi.nlm.nih.gov/38517496/', excerpt:'Source abstract', providers:['pubmed','europepmc'], journal:'Journal', year:'2024', pmid:'38517496', authors:['An Author'], retrievedAt:'2026-10-04T10:00:00Z' };
const contact: Source = { id:'centre-1', kind:'contact', title:'Source-listed centre', url:'https://www.orpha.net/en/expert-centres/centre/123', excerpt:'A centre associated with this condition', providers:['orphanet_groups'], recordType:'expert centre', location:'France' };
const response = (sources = [paper]) => ({status:'ok',query:'Marfan syndrome',pipeline:'repository-literature',providers:['pubmed'],unavailableProviders:[],sources,retrievedAt:paper.retrievedAt,cached:false});

test('literature requests use the configured service and retain publication provenance', async t => {
  t.mock.method(globalThis,'fetch',async (url: URL, init: RequestInit) => {
    assert.equal(url.pathname,'/api/research/papers');
    assert.equal(init.redirect,'error');
    assert.deepEqual(JSON.parse(String(init.body)),{query:'Marfan syndrome',limit:5,category:'all'});
    return Response.json(response());
  });
  const data = await searchResearch(' Marfan syndrome ',{papers:true,limit:5});
  assert.deepEqual(data.sources[0],paper);
});

test('unavailable, malformed and private backend records cannot masquerade as empty results', async t => {
  for (const payload of [response([{...paper,url:'javascript:alert(1)'}]), response([{...paper,url:'/api/files/private',kind:'workspace'}]), {...response(),pipeline:'repository-graph'}, {status:'unavailable'}, {}]) {
    const mock = t.mock.method(globalThis,'fetch',async () => Response.json(payload));
    await assert.rejects(searchResearch('Marfan',{papers:true}));
    mock.mock.restore();
  }
  t.mock.method(globalThis,'fetch',async () => new Response('unavailable',{status:503}));
  await assert.rejects(searchResearch('Marfan',{papers:true}),/temporarily unavailable/);
});

test('public search terms cannot select a URL or send document-sized text', async t => {
  let calls = 0;
  t.mock.method(globalThis,'fetch',async () => {calls++;return Response.json(response());});
  for(const term of ['https://example.test', 'query\nprivate document', 'x'.repeat(201), 'a']) await assert.rejects(searchResearch(term));
  assert.equal(calls,0);
});

test('partial provider failures preserve genuine results and explain coverage', async t => {
  t.mock.method(globalThis,'fetch',async () => Response.json({...response(),status:'partial',unavailableProviders:['pubtator']}));
  const result = await searchResearch('Marfan',{papers:true});
  assert.equal(result.sources.length,1);
  assert.match(researchWarning(result)!,/pubtator/);
});

test('cards appear after cited paragraphs once and uncited retrieved records remain visible', () => {
  const parts=answerWithSources('Paper evidence [1].\n\nCentre information [2].\n\nSee [1, 2] again.',[paper,contact]);
  assert.deepEqual(parts.map(p=>p.cards.map(c=>c.source.id)),[['paper-1'],['centre-1'],[]]);
  assert.equal(answerWithSources('No citation.',[paper])[1].cards[0].source.id,'paper-1');
  const code=answerWithSources('```txt\n[1]\n\ncode\n```\n\nRead [2](https://example.test).',[paper,contact]);
  assert.equal(code[0].cards.length,0);
  assert.equal(code.at(-1)!.cards.length,2);
});

test('invalid and duplicate model citations cannot shift a card onto the wrong record', () => {
  assert.equal(remapCitations('Evidence [1, 2, 3]. Again [4].',['invented','paper-1','centre-1','paper-1'],[paper,contact]),'Evidence [1, 2]. Again [1].');
});

test('paper and contact widgets render source metadata and escape untrusted source content', () => {
  const html=renderToStaticMarkup(createElement(SourceCard,{source:{...paper,title:'<script>bad</script>'},number:1}));
  assert.match(html,/evidence-card/);
  assert.match(html,/Journal · 2024/);
  assert.match(html,/pubmed.ncbi.nlm.nih.gov\/38517496/);
  assert.ok(!html.includes('<script>bad</script>'));
  const centre=renderToStaticMarkup(createElement(SourceCard,{source:contact}));
  assert.match(centre,/expert centre · France/);
  assert.ok(!centre.includes('mailto:'));
  assert.throws(()=>sourceSchema.parse({...paper,url:'https://password:secret@example.test'}));
});
