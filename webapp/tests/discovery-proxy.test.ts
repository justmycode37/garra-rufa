import test from 'node:test';
import assert from 'node:assert/strict';
import { GET, POST } from '../src/app/api/discovery/route';
import regions from '../src/lib/body-regions.json';

test('discovery proxy restricts resources and carries bounded identifiers to the fixed backend', async t => {
  let target='';
  t.mock.method(globalThis,'fetch',async(url:URL)=>{target=String(url);return Response.json({region:{id:'heart'},diseases:[]});});
  const response=await GET(new Request('http://localhost/api/discovery?resource=region&id=heart'));
  assert.equal(response.status,200);assert.equal(new URL(target).pathname,'/api/regions/heart');
  assert.equal((await GET(new Request('http://localhost/api/discovery?resource=region&id=../../private'))).status,400);
  assert.equal((await GET(new Request('http://localhost/api/discovery?resource=https://evil.example'))).status,400);
  assert.equal((await GET(new Request('http://localhost/api/discovery?resource=search'))).status,400);
});
test('text search is posted only to unified search and backend errors remain visible',async t=>{
  t.mock.method(globalThis,'fetch',async(url:URL,options:RequestInit)=>{assert.equal(new URL(url).pathname,'/api/search');assert.equal(JSON.parse(String(options.body)).query,'Pompe');return Response.json({error:'not ready'},{status:503});});
  const response=await POST(new Request('http://localhost/api/discovery?resource=search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:'Pompe'})}));
  assert.equal(response.status,503);assert.equal((await response.json()).error,'not ready');
});
test('iris uses a specific root and broad substructure mappings disclose their scope',()=>{
 assert.equal(regions.iris.hpo,'HP:0000525');assert.notEqual(regions.iris.hpo,regions.eyes.hpo);
 assert.ok(regions['hand-bones'].scope);assert.ok(regions.airways.scope);
});
