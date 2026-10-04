import test from 'node:test';
import assert from 'node:assert/strict';
import { atlasContextForTarget, atlasResources, discoverAtlas } from '../src/lib/atlas-discovery';

test('questions focus named organs and expose only their linked conditions', () => {
  const result = discoverAtlas('Show me research about the heart');
  assert.equal(result.context?.target, 'heart');
  assert.deepEqual(result.context?.diseaseIds, ['fabry', 'marfan', 'pompe']);
  assert.ok(result.resources.some(resource => resource.kind === 'paper'));
  assert.equal(discoverAtlas('What about the aorta?').context?.target, 'aorta');
});

test('condition names and genes resolve without mistaking parts of the name for anatomy', () => {
  assert.equal(discoverAtlas('Tell me about Charcot-Marie-Tooth disease').context?.target, 'hands');
  assert.equal(discoverAtlas('Tell me about MECP2').context?.target, 'brain');
  assert.equal(discoverAtlas('Explore spinal muscular atrophy').context?.diseaseId, 'sma');
  assert.equal(discoverAtlas('What is a small molecule?').context, undefined);
});

test('follow-ups keep the selected anatomy but unknown conditions do not reuse it', () => {
  const heart = discoverAtlas('Explore Fabry in the heart').context;
  assert.equal(discoverAtlas('Show me its papers', heart).context?.target, 'heart');
  assert.equal(discoverAtlas('Explain the genes linked to Fabry', heart).context?.target, 'heart');
  assert.equal(discoverAtlas('Explain Gaucher disease', heart).context, undefined);
});

test('comparisons use a shared region and do not invent a link to an explicit unrelated organ', () => {
  assert.equal(discoverAtlas('Compare Fabry and Pompe').context?.target, 'heart');
  assert.equal(discoverAtlas('Compare Huntington and Pompe').context?.target, 'body');
  const unrelated = discoverAtlas('Show Huntington in the heart');
  assert.equal(unrelated.context?.target, 'heart');
  assert.deepEqual(unrelated.context?.diseaseIds, []);
  assert.deepEqual(unrelated.resources, []);
});

test('body selection produces a reading list and a selected paper supports a contextual follow-up', () => {
  const context = atlasContextForTarget('hand-bones', 'Hand bones');
  assert.ok(context.diseaseIds.includes('cmt'));
  const resources = atlasResources('cmt');
  for (const kind of ['paper', 'gene', 'reference']) assert.ok(resources.some(resource => resource.kind === kind));
  const paper = resources.find(resource => resource.kind === 'paper')!;
  assert.equal(new URL(paper.url).hostname, 'pubmed.ncbi.nlm.nih.gov');
  const reply = discoverAtlas('Explain this paper', { ...context, diseaseId: 'cmt' }, paper);
  assert.ok(reply.text.includes(paper.description));
  assert.equal(reply.context?.target, 'hand-bones');
});

test('uncovered body parts keep an honest empty state', () => {
  const result = discoverAtlas('Show me the pancreas');
  assert.equal(result.context?.target, 'pancreas');
  assert.deepEqual(result.resources, []);
  assert.match(result.text, /no linked condition or paper records/);
});
