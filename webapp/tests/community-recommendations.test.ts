import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as store from '../src/lib/store';
import { listConditions, resolveCondition, saveCommunityProfile, addCommunityPost } from '../src/lib/conditions';
import { recommendCommunities } from '../src/lib/community-recommendations';
import { conversationTransferSchema, saveLandingConversation } from '../src/lib/chat-transfer';
import { Answer } from '../src/components/Chat';

const directory = mkdtempSync(path.join(tmpdir(), 'garra-community-recommendations-'));
process.env.GARRA_DATA_DIR = directory;
process.env.DATA_ENCRYPTION_KEY = randomBytes(32).toString('hex');
after(() => { store.db().close(); rmSync(directory, { recursive: true, force: true }); });

test('disease names, aliases, punctuation and abbreviations match existing communities only', () => {
  const conditions = listConditions();
  for (const query of ['Explain Fabry disease', 'FABRY', 'How about fabry-disease?']) assert.deepEqual(recommendCommunities(query, conditions).map(c => c.id), ['fabry']);
  assert.deepEqual(recommendCommunities('Huntington’s disease and Ehlers Danlos syndrome', conditions).map(c => c.id), ['huntington', 'eds']);
  assert.deepEqual(recommendCommunities('What is SMA?', conditions).map(c => c.id), ['sma']);
  for (const query of ['plasma results', 'What causes heart pain?', 'An unknown disease', 'Tell me about communities']) assert.deepEqual(recommendCommunities(query, conditions), []);
});

test('custom conditions keep real counts, deduplicate aliases and prefer the named subtype', () => {
  const user = store.createGuest('patient');
  const parent = resolveCondition({ conditionName: 'Example syndrome' }, user.id)!;
  const child = resolveCondition({ conditionName: 'Example syndrome type 2' }, user.id)!;
  saveCommunityProfile(user.id, { diseaseId: child.id, alias: 'Member', bio: 'Never expose a member profile in a recommendation' });
  addCommunityPost(user.id, child.id, 'A community-only post');
  const conditions = listConditions();
  const recommended = recommendCommunities('Tell me about Example-syndrome type 2', conditions);
  assert.deepEqual(recommended, [{ id: child.id, name: child.name, memberCount: 1, postCount: 1 }]);
  assert.deepEqual(recommendCommunities('Example syndrome and Example syndrome type 2', conditions).map(c => c.id), [parent.id, child.id]);
  assert.equal(recommendCommunities('Fabry and Fabry disease and fabry', conditions).length, 1);
  assert.equal(recommendCommunities('Fabry Pompe Marfan Rett Huntington', conditions).length, 3);
});

test('recommendations survive conversation transfer with canonical metadata and unknown IDs removed', () => {
  const user = store.createGuest('patient');
  const input = conversationTransferSchema.parse({ messages: [
    { id: 'question', role: 'user', text: 'Fabry disease' },
    { id: 'answer', role: 'assistant', text: 'An answer.', communities: [{ id: 'fabry', name: 'Forged label', memberCount: 99999 }, { id: 'not-real' }] },
  ] });
  const record = saveLandingConversation(user.id, input);
  const saved = store.getRecord(record.id, user.id)?.messages?.at(-1)?.communities;
  assert.deepEqual(saved, recommendCommunities('Fabry', listConditions()));
});

test('completed chat answers show a matching evidence-style widget with a direct community link', () => {
  const message = { id: 'answer', role: 'assistant' as const, text: 'An answer.', communities: [{ id: 'fabry', name: 'Fabry disease', memberCount: 1, postCount: 2 }] };
  const html = renderToStaticMarkup(createElement(Answer, { message, plain: true, onDisease: () => {} }));
  assert.match(html, /Recommended communities/);
  assert.match(html, /evidence-card answer-community/);
  assert.match(html, /href="\/\?view=community&amp;condition=fabry"/);
  assert.match(html, /1 member · 2 posts/);
  assert.match(html, /Explore community/);
  assert.doesNotMatch(renderToStaticMarkup(createElement(Answer, { message: { ...message, streaming: true }, onDisease: () => {} })), /Recommended communities/);
});
