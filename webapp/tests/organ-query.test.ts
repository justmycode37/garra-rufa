import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { MAX_QUERY, organFindingsSchema, organQuery, readTerm } from '../src/lib/organ-query';
import regions from '../src/lib/body-regions.json';

const heart = { label: 'Heart', hpo: 'HP:0001627' };

test('an organ query needs at least one term besides the organ', () => {
  assert.ok(organQuery(heart, []).error);
  assert.deepEqual(organQuery(heart, [{ text: 'chest pain' }]), { query: 'HP:0001627, chest pain', label: 'Heart: chest pain' });
});

test('picked findings go in by HP id, typed terms as text, duplicates once', () => {
  const request = organQuery(heart, [{ text: 'Cardiomyopathy', id: 'HP:0001638' }, { text: 'MYH7' }, { text: 'myh7' }]);
  assert.deepEqual(request, { query: 'HP:0001627, HP:0001638, MYH7', label: 'Heart: Cardiomyopathy, MYH7' });
});

test('the query fits the research service limit and its characters', () => {
  const long = Array.from({ length: 12 }, (_, i) => ({ text: `rather long symptom ${i}` }));
  assert.match(organQuery(heart, long).error ?? '', /Too many/);
  assert.ok((organQuery(heart, long.slice(0, 3)).query ?? '').length <= MAX_QUERY);
  assert.equal(readTerm('  chest   pain ').term, 'chest pain');
  for (const bad of ['a', 'pain, fever', 'see https://x', '-flag', 'x'.repeat(61)]) assert.ok(readTerm(bad).error, bad);
  assert.equal(readTerm('').term, undefined);
});

test('every atlas region has exported finding suggestions', () => {
  const data = organFindingsSchema.parse(JSON.parse(readFileSync(new URL('../public/organ-findings.json', import.meta.url), 'utf8')));
  for (const [id, region] of Object.entries(regions)) {
    assert.ok(data.regions[id], id);
    assert.ok(data.regions[id].diseases > 0, id);
    assert.ok(data.regions[id].findings.every(finding => finding.id !== region.hpo), id);
  }
});
