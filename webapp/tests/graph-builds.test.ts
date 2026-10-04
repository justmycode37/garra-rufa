import test from 'node:test';
import assert from 'node:assert/strict';
import { candidateQuery } from '../src/lib/graph-builds';

test('a candidate disease is built from its name alone, in the characters the service accepts', () => {
  assert.equal(candidateQuery('Cardiomyopathy, dilated, 3C'), 'Cardiomyopathy, dilated, 3C');
  assert.equal(candidateQuery('Sudden cardiac failure, alcohol-induced'), 'Sudden cardiac failure, alcohol-induced');
  assert.equal(candidateQuery('Müller–Weiss [disease] & co'), 'Müller Weiss disease co');
  assert.equal(candidateQuery('-- [x] Name'), 'x Name');
  assert.ok(candidateQuery('a'.repeat(300)).length <= 120);
});
