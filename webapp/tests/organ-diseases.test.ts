import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { diseaseGraphHref, diseaseRequest, findDiseaseRun, organDiseaseListSchema } from '../src/lib/organ-diseases';
import regions from '../src/lib/body-regions.json';

test('an atlas disease link round-trips to its identifiers and name', () => {
  const href = diseaseGraphHref({ id: 'OMIM:154700', ids: ['OMIM:154700', 'ORPHA:558'], name: 'Marfan syndrome' });
  assert.ok(href.startsWith('/graphs/overview?'));
  assert.deepEqual(diseaseRequest(new URL(href, 'http://x').searchParams), { ids: ['OMIM:154700', 'ORPHA:558'], name: 'Marfan syndrome' });
});

test('disease requests drop anything that is not a disease identifier', () => {
  assert.equal(diseaseRequest(new URLSearchParams({ disease: 'http://evil,--flag', name: 'x' })), undefined);
  assert.deepEqual(diseaseRequest(new URLSearchParams({ disease: 'ORPHA:558,../x', name: 'a\nb' })), { ids: ['ORPHA:558'], name: 'a b' });
});

test('an existing graph is found by identifier, build query or synonym', () => {
  const runs = [
    { id: 'marfan', label: 'Marfan syndrome', ids: ['MONDO:0007947', 'ORPHA:558'], names: ['Marfan syndrome', "Marfan's syndrome"] },
    { id: 'orpha-79318-abcd1234', label: 'PMM2-CDG', query: 'ORPHA:79318' },
  ];
  assert.equal(findDiseaseRun(runs, { ids: ['OMIM:154700', 'ORPHA:558'], name: 'x' })?.id, 'marfan');
  assert.equal(findDiseaseRun(runs, { ids: ['ORPHA:79318'], name: 'x' })?.id, 'orpha-79318-abcd1234');
  assert.equal(findDiseaseRun(runs, { ids: ['OMIM:1'], name: 'MARFAN SYNDROME' })?.id, 'marfan');
  assert.equal(findDiseaseRun(runs, { ids: ['OMIM:1'], name: 'Holt-Oram syndrome' }), undefined);
});

test('every atlas region has an exported disease list', () => {
  for (const id of Object.keys(regions)) {
    const list = organDiseaseListSchema.parse(JSON.parse(readFileSync(new URL(`../public/organ-diseases/${id}.json`, import.meta.url), 'utf8')));
    assert.equal(list.region, id);
    assert.equal(list.total, list.diseases.length);
    assert.ok(list.total > 0, id);
    assert.equal(new Set(list.diseases.map(d => d.id)).size, list.total, id);
  }
});
