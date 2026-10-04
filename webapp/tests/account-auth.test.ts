import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { authWorkspaceUser } from '../src/lib/auth-user';
import * as store from '../src/lib/store';
import { readCompletedResponse } from '../src/lib/openai-response';

const directory = mkdtempSync(path.join(tmpdir(), 'garra-auth-tests-'));
process.env.GARRA_DATA_DIR = directory;
process.env.GARRA_STORAGE = 'sqlite';
process.env.DATA_ENCRYPTION_KEY = '34'.repeat(32);
test.after(() => { store.db().close(); rmSync(directory, { recursive: true, force: true }); });

test('Auth subject owns a stable workspace and role is not reset on subsequent requests', async () => {
  const identity = { id: randomUUID(), email: 'new@example.test', name: 'New member' };
  const first = await authWorkspaceUser(identity, 'doctor');
  assert.equal(first.needsRole, false);
  const record = store.saveRecord(first.id, { kind: 'note', title: 'Private note' });
  const second = await authWorkspaceUser(identity, 'patient');
  assert.equal(second.id, identity.id);
  assert.equal(second.role, 'doctor');
  assert.equal(store.getRecord(record.id, second.id)?.title, 'Private note');
});

test('a matching legacy email never grants access to the old account', async () => {
  const legacy = store.createUser('Legacy', 'legacy@example.test', 'synthetic-password', 'doctor');
  const privateRecord = store.saveRecord(legacy.id, { kind: 'patient', title: 'Legacy private record' });
  const member = await authWorkspaceUser({ id: randomUUID(), email: legacy.email, name: 'New account' });
  assert.notEqual(member.id, legacy.id);
  assert.equal(store.getRecord(privateRecord.id, member.id), null);
  assert.equal(store.getRecord(privateRecord.id, legacy.id)?.title, 'Legacy private record');
});

test('separate Auth IDs cannot access another member’s records', async () => {
  const a = await authWorkspaceUser({ id: randomUUID(), email: 'a@example.test', name: 'A' });
  const b = await authWorkspaceUser({ id: randomUUID(), email: 'b@example.test', name: 'B' });
  const record = store.saveRecord(a.id, { kind: 'note', title: 'Only A' });
  assert.equal(store.getRecord(record.id, b.id), null);
  assert.deepEqual(store.listRecords(b.id), []);
});

test('malformed identity inputs never create a workspace', async () => {
  await assert.rejects(authWorkspaceUser({ id: 'not-a-uuid', email: 'a@example.test', name: 'A' }));
});

test('API stream failure never commits partial text or exposes provider diagnostics', async () => {
  const stream = new Response('data: {"type":"response.output_text.delta","delta":"partial"}\n\ndata: {"type":"response.failed","response":{"error":{"message":"secret diagnostic"}}}\n\n');
  await assert.rejects(readCompletedResponse(stream), error => error instanceof Error && !error.message.includes('secret diagnostic'));
  await assert.rejects(readCompletedResponse(new Response('data: {"type":"response.incomplete"}\n\n')), /did not finish/);
});
