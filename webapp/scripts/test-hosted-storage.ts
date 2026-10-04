// Live contract test. Creates only uniquely tagged synthetic records and removes
// those records in finally. Never imports or uploads the local SQLite database.
import assert from 'node:assert/strict';
import { randomBytes, randomUUID } from 'node:crypto';
import { createClient } from '@supabase/supabase-js';
import * as store from '../src/lib/persistence';
import { admin, data } from '../src/lib/supabase/admin';
import type { SignInAttempt, ChatGPTTokens } from '../src/lib/chatgpt-store';

async function main() {
process.env.GARRA_STORAGE = 'supabase';
process.env.DATA_ENCRYPTION_KEY ||= randomBytes(32).toString('hex');
const run = randomUUID();
const users: string[] = [];
let attemptId = '';
let conditionId = '';
let checks = 0;
const check = (label: string) => { checks++; console.log(`PASS ${label}`); };
try {
  const owner = await store.createGuest('researcher'); users.push(owner.id);
  const other = await store.createUser('Storage test recipient', `${run}@example.invalid`, randomBytes(32).toString('hex'), 'doctor'); users.push(other.id);
  const session = await store.createSession(owner.id);
  assert.equal((await store.sessionUser(session))?.id, owner.id);
  await store.removeSession(session); assert.equal(await store.sessionUser(session), null);
  check('sessions persist and revoke');
  const record = await store.saveRecordWithCondition(owner.id, { kind: 'project', title: `Storage test ${run}`, content: 'Synthetic test content', conditionName: `Storage test ${run}` });
  conditionId = record.diseaseId!;
  assert.equal(await store.getRecord(record.id, other.id), null);
  await assert.rejects(store.saveRecord(other.id, { id: record.id, kind: 'project', title: 'Forbidden' }));
  await store.deleteRecord(record.id, other.id); assert.ok(await store.getRecord(record.id, owner.id));
  check('another user cannot read, modify, or delete private records');
  await store.storeFile(record.id, Buffer.from('Synthetic bytes'));
  assert.equal(await store.getFile(record.id, other.id), null);
  await store.shareRecord(record.id, owner.id, other.email);
  assert.equal((await store.getRecord(record.id, other.id))?.readOnly, true);
  assert.equal((await store.getFile(record.id, other.id))?.toString(), 'Synthetic bytes');
  await assert.rejects(store.shareRecord(record.id, other.id, owner.email));
  await store.shareRecord(record.id, owner.id, other.email, true);
  assert.equal(await store.getFile(record.id, other.id), null);
  check('sharing grants read access and revocation removes file access');
  await store.publishRecord(record.id, owner.id, true);
  assert.equal((await store.publicProjects(conditionId))[0]?.id, record.id);
  await store.publishRecord(record.id, owner.id, false);
  assert.equal((await store.publicProjects(conditionId)).length, 0);
  check('only explicitly published projects are public');
  const raw = await data<{payload:string}>(admin().from('garra_records').select('payload').eq('id', record.id).single());
  assert.equal(raw.payload.includes('Synthetic test content'), false);
  check('private payload is encrypted in Postgres');
  await store.saveCommunityProfile(owner.id, { diseaseId: conditionId, alias: 'Synthetic member', bio: 'Test' });
  const post = await store.addCommunityPost(owner.id, conditionId, 'Synthetic post');
  await assert.rejects(store.deleteCommunityPost(other.id, post.id));
  await assert.rejects(store.addCommunityPost(other.id, conditionId, 'Not a member'));
  assert.equal((await store.communityData(owner.id, conditionId)).posts.length, 1);
  await store.deleteCommunityPost(owner.id, post.id);
  await store.leaveCommunity(owner.id, conditionId);
  check('community membership and post ownership are enforced');
  const attempt: SignInAttempt = { state: run, nonce: run, verifier: run, redirectUri: 'http://127.0.0.1:3000/api/auth/chatgpt/callback', role: 'researcher', browserId: run, guestUserId: owner.id, clientId: 'oaiapp_test' };
  attemptId = await store.saveAttempt(attempt);
  const consumed = await Promise.allSettled([store.consumeAttempt(attemptId, run), store.consumeAttempt(attemptId, run)]);
  assert.equal(consumed.filter(result => result.status === 'fulfilled').length, 1);
  check('OAuth attempts are consumed atomically');
  const tokens: ChatGPTTokens = { accessToken: 'synthetic', refreshToken: 'synthetic', idToken: 'synthetic', scopes: ['chatgpt.tokens.use.direct'], expiresAt: Date.now() + 600000 };
  const identity = { issuer: 'https://auth.openai.com', clientId: `oaiapp_${run}`, subject: run, name: 'Storage test identity', email: `${run}@test.invalid`, emailVerified: true };
  let account = await store.connectAccount(identity, tokens, attempt);
  assert.equal(account.userId, owner.id);
  assert.equal((await store.browserAccounts(run)).length, 1);
  assert.equal((await store.browserAccounts('unrelated')).length, 0);
  const locks = await Promise.all([store.acquireRefresh(account), store.acquireRefresh(account)]);
  assert.equal(locks.filter(Boolean).length, 1); await store.releaseRefresh(account.id);
  const old = account; account = await store.saveAccount({ ...account, welcomed: true });
  await assert.rejects(store.saveAccount(old));
  const connected = await store.createSession(owner.id);
  assert.equal((await store.workspaceUser(connected))?.chatgpt?.planEnabled, true);
  check('identity adoption, browser scoping, and concurrent token refresh are safe');
  const allowed = await Promise.all(Array.from({ length: 10 }, () => store.limit(`test:${run}`, 3, 60000)));
  assert.equal(allowed.filter(Boolean).length, 3);
  check('rate limits are atomic across concurrent requests');
  const publicClient = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!, { auth: { persistSession: false } });
  const denied = await publicClient.from('garra_records').select('*');
  assert.ok(denied.error || !denied.data?.length);
  const deniedRpc = await publicClient.rpc('garra_accessible_records', { p_user: owner.id });
  assert.ok(deniedRpc.error);
  check('publishable keys cannot access private tables or server functions');
  await store.deleteRecord(record.id, owner.id);
  await assert.rejects(store.saveRecord(owner.id, { id: record.id, kind: 'project', title: 'Do not resurrect' }));
  check('deleted records cannot be recreated by stale saves');
  console.log(`${checks} hosted storage checks passed.`);
} finally {
  // IDs came only from test-created entities; cascade removes their test files,
  // shares, browser bindings, account rows and sessions.
  if (users.length) await data(admin().from('garra_users').delete().in('id', users));
  if (conditionId) await data(admin().from('garra_conditions').delete().eq('id', conditionId));
  await data(admin().from('garra_limits').delete().in('bucket', [`test:${run}`, ...users.flatMap(id => [`condition-create:${id}`, `community-post:${id}`])]));
}

}
main().catch(error=>{console.error(error instanceof Error ? error.message : 'Hosted test failed');process.exitCode=1;});
