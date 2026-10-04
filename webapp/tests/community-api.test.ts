import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createCommunityHandlers } from '../src/lib/community-api';
import { HttpError } from '../src/lib/http';
import * as store from '../src/lib/store';
import * as persistence from '../src/lib/persistence';

const directory = mkdtempSync(path.join(tmpdir(), 'garra-community-api-'));
process.env.GARRA_DATA_DIR = directory;
process.env.GARRA_STORAGE = 'sqlite';
process.env.DATA_ENCRYPTION_KEY = '56'.repeat(32);
after(() => { store.db().close(); rmSync(directory, { recursive: true, force: true }); });

const member = store.createUser('Member', 'member@example.test', 'synthetic-password', 'patient');
const other = store.createUser('Other', 'other@example.test', 'synthetic-password', 'researcher');
const api = createCommunityHandlers(async () => member);
const otherApi = createCommunityHandlers(async () => other);
function request(method = 'GET', input?: unknown, query = '') {
  return new Request(`http://127.0.0.1:3000/api/community${query}`, {
    method, headers: { 'Content-Type': 'application/json' },
    body: input === undefined ? undefined : JSON.stringify(input),
  });
}
async function join(diseaseId: string, alias = 'River') {
  const response = await api.POST(request('POST', { action: 'profile', diseaseId, alias, bio: 'Here to connect' }));
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.ok, true);
  assert.equal(result.profile.diseaseId, diseaseId);
  assert.equal(result.profile.alias, alias);
  assert.equal(result.condition.id, diseaseId);
  assert.equal((await persistence.getCommunityProfile(member.id, diseaseId))?.alias, alias);
}

test('directory response contains the database communities, including user-added conditions', async () => {
  const saved = await persistence.saveRecordWithCondition(member.id, {
    kind: 'note', title: 'Private record', conditionName: 'Database-only test condition', content: 'Never public',
  });
  const response = await api.GET(request());
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  const result = await response.json();
  assert.deepEqual(result.conditions.map((item: { id: string }) => item.id), (await persistence.listConditions()).map(item => item.id));
  assert.ok(result.conditions.some((item: { id: string }) => item.id === saved.diseaseId));
  assert.ok(result.conditions.some((item: { id: string }) => item.id === 'fabry'));
  assert.deepEqual(result.profiles, []);
  assert.deepEqual(result.posts, []);
  assert.deepEqual(result.projects, []);
  assert.ok(!JSON.stringify(result).includes('Never public'));
});

test('join returns a saved profile, preserves other memberships, and survives reload', async () => {
  await join('fabry');
  await join('pompe');
  await join('fabry', 'Updated alias');
  const response = await api.GET(request('GET', undefined, '?conditionId=fabry'));
  const result = await response.json();
  assert.equal(result.condition.id, 'fabry');
  assert.equal(result.profile.alias, 'Updated alias');
  assert.equal(result.profiles.length, 2);
  assert.equal(result.members[0].alias, 'Updated alias');
  assert.equal(result.conditions.find((item: { id: string }) => item.id === 'fabry').memberCount, 1);
  for (const id of ['fabry', 'pompe']) assert.equal(result.conditions.find((item: { id: string }) => item.id === id).joined, true);
  const otherResult = await (await otherApi.GET(request())).json();
  assert.deepEqual(otherResult.profiles, []);
  assert.ok(otherResult.conditions.every((item: { joined: boolean }) => !item.joined));
});

test('posts return persisted data and deletion waits for ownership checks', async () => {
  const response = await api.POST(request('POST', { action: 'post', conditionId: 'fabry', content: 'Synthetic test conversation' }));
  assert.equal(response.status, 200);
  const { post } = await response.json();
  assert.equal(post.content, 'Synthetic test conversation');
  assert.equal(post.conditionId, 'fabry');
  assert.equal((await persistence.communityData(member.id, 'fabry')).posts[0].id, post.id);
  const denied = await otherApi.DELETE(request('DELETE', { postId: post.id }));
  assert.equal(denied.status, 404);
  assert.equal((await denied.json()).error, 'Post not found.');
  assert.equal((await persistence.communityData(member.id, 'fabry')).posts.length, 1);
  assert.equal((await api.DELETE(request('DELETE', { postId: post.id }))).status, 200);
  assert.deepEqual((await persistence.communityData(member.id, 'fabry')).posts, []);
});

test('leaving one community preserves the other and prevents new posts', async () => {
  const response = await api.DELETE(request('DELETE', { conditionId: 'fabry' }));
  assert.equal(response.status, 200);
  const result = await (await api.GET(request())).json();
  assert.deepEqual(result.profiles.map((item: { diseaseId: string }) => item.diseaseId), ['pompe']);
  assert.equal(result.conditions.find((item: { id: string }) => item.id === 'fabry').joined, false);
  const denied = await api.POST(request('POST', { action: 'post', conditionId: 'fabry', content: 'After leaving' }));
  assert.equal(denied.status, 403);
  assert.match((await denied.json()).error, /Join this community/);
});

test('asynchronous condition errors keep their HTTP status and never report success', async () => {
  const missing = await api.GET(request('GET', undefined, '?conditionId=unknown'));
  assert.equal(missing.status, 404);
  assert.deepEqual(await missing.json(), { error: 'Community not found.' });
  const invalid = await api.POST(request('POST', { action: 'profile', diseaseId: 'unknown', alias: 'Member', bio: '' }));
  assert.equal(invalid.status, 400);
  assert.match((await invalid.json()).error, /Choose an existing/);
});

test('all community methods require sign-in and reject cross-site mutations', async () => {
  const anonymous = createCommunityHandlers(async () => { throw new HttpError(401, 'Please sign in to continue.'); });
  for (const method of ['GET', 'POST', 'DELETE'] as const) {
    assert.equal((await anonymous[method](request(method))).status, 401);
  }
  const crossSite = request('POST', { action: 'profile', diseaseId: 'rett', alias: 'Member', bio: '' });
  crossSite.headers.set('Origin', 'https://example.com');
  assert.equal((await api.POST(crossSite)).status, 403);
  assert.equal(await persistence.getCommunityProfile(member.id, 'rett'), null);
});
