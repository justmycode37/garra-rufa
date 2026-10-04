import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { CACHE_SIZE, cachedExample, forget, isBuildId, readCache, remember, type ExampleEntry } from '../src/lib/graph-cache';

test('the cache keeps queried graphs most recent first, once each and bounded', () => {
  let ids: string[] = [];
  for (let i = 0; i < CACHE_SIZE + 3; i++) ids = remember(ids, `graph-${i}`);
  assert.equal(ids.length, CACHE_SIZE);
  assert.equal(ids[0], `graph-${CACHE_SIZE + 2}`);
  assert.deepEqual(remember(['a', 'b', 'c'], 'c'), ['c', 'a', 'b']);
  assert.deepEqual(forget(['a', 'b'], 'a'), ['b']);
  assert.deepEqual(remember(['a'], '../x'), ['a']);
});

test('a stored cache is read defensively', () => {
  assert.deepEqual(readCache(null), []);
  assert.deepEqual(readCache('not json'), []);
  assert.deepEqual(readCache('{"a":1}'), []);
  assert.deepEqual(readCache('["pompe", "pompe", 3, "../etc", "marfan-syndrome-1a2b3c4d"]'), ['pompe', 'marfan-syndrome-1a2b3c4d']);
  assert.ok(isBuildId('marfan-syndrome-1a2b3c4d'));
  assert.ok(!isBuildId('pompe'));
});

test('a query naming a bundled example opens it without a build', () => {
  const index: ExampleEntry[] = JSON.parse(readFileSync(new URL('../public/graph-data/index.json', import.meta.url), 'utf8'));
  assert.equal(cachedExample(index, 'Marfan syndrome')?.id, 'marfan');
  assert.equal(cachedExample(index, '  orpha:558 ')?.id, 'marfan');
  assert.equal(cachedExample(index, 'glycogen-storage disease II', true)?.id, 'pompe');
  assert.equal(cachedExample(index, 'Marfan')?.id, 'marfan');
  assert.equal(cachedExample(index, 'Marfan syndrome, neonatal'), undefined);
  const noEvidence: ExampleEntry[] = [{ id: 'x', label: 'X disease', present: 'x.json', evidence: null }];
  assert.equal(cachedExample(noEvidence, 'x disease')?.id, 'x');
  assert.equal(cachedExample(noEvidence, 'x disease', true), undefined);
});
