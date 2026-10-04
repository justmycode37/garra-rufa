import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import * as store from '../src/lib/store';
import { conversationTransferSchema, ConversationUnavailable, saveLandingConversation } from '../src/lib/chat-transfer';
import { diseases } from '../src/lib/knowledge';

const directory = mkdtempSync(path.join(tmpdir(), 'garra-chat-transfer-'));
process.env.GARRA_DATA_DIR = directory;
process.env.DATA_ENCRYPTION_KEY = randomBytes(32).toString('hex');
after(() => { store.db().close(); rmSync(directory, { recursive: true, force: true }); });

const messages = [
  { id: 'question', role: 'user', text: 'Help me understand Fabry disease.' },
  { id: 'answer', role: 'assistant', text: '## Overview\n\nA **formatted** answer.', sources: [{ id: 'source', title: 'Reference', url: 'https://example.org/reference', kind: 'reference', excerpt: 'Evidence' }], diseases: [diseases[0]], steps: ['Read the source.'], mode: 'ai', suggestion: { kind: 'note', title: 'Notes', content: 'Keep this answer.' } },
];

test('landing conversations survive entry into each role with answer metadata intact', () => {
  for (const role of ['researcher', 'doctor', 'patient'] as const) {
    const user = store.createGuest(role);
    const record = saveLandingConversation(user.id, conversationTransferSchema.parse({ messages }));
    assert.equal(record.kind, 'chat');
    assert.equal(record.visibility, 'private');
    assert.deepEqual(store.getRecord(record.id, user.id)?.messages, messages);
    assert.equal(store.listRecords(user.id)[0].title, messages[0].text);
    store.updateGuestRole(user.id, role === 'doctor' ? 'patient' : 'doctor');
    assert.deepEqual(store.getRecord(record.id, user.id)?.messages, messages);
  }
});

test('existing landing chats continue in the same saved record without duplicates', () => {
  const user = store.createGuest('researcher');
  const record = saveLandingConversation(user.id, conversationTransferSchema.parse({ messages }));
  const followup = [...messages, { id: 'followup', role: 'user', text: 'What should I read next?' }, { id: 'reply', role: 'assistant', text: 'Here is a source.' }];
  const continued = saveLandingConversation(user.id, conversationTransferSchema.parse({ chatId: record.id, messages: followup }));
  assert.equal(continued.id, record.id);
  assert.equal(store.listRecords(user.id).length, 1);
  assert.equal(continued.messages?.length, 4);
});

test('conversation transfer cannot overwrite another owner, a non-chat record, or a deleted chat', () => {
  const owner = store.createGuest('patient'), other = store.createGuest('doctor');
  const chat = saveLandingConversation(owner.id, conversationTransferSchema.parse({ messages }));
  const note = store.saveRecord(owner.id, { kind: 'note', title: 'Preserve this note' });
  assert.throws(() => saveLandingConversation(other.id, conversationTransferSchema.parse({ chatId: chat.id, messages })), ConversationUnavailable);
  assert.throws(() => saveLandingConversation(owner.id, conversationTransferSchema.parse({ chatId: note.id, messages })), ConversationUnavailable);
  store.deleteRecord(chat.id, owner.id);
  assert.throws(() => saveLandingConversation(owner.id, conversationTransferSchema.parse({ chatId: chat.id, messages })), ConversationUnavailable);
});

test('transfer validates completed conversations and citation URLs', () => {
  assert.equal(conversationTransferSchema.safeParse({ messages: [] }).success, false);
  assert.equal(conversationTransferSchema.safeParse({ messages: messages.slice(0, 1) }).success, false);
  const invalid = [messages[0], { ...messages[1], sources: [{ ...messages[1].sources![0], url: 'javascript:alert(1)' }] }];
  assert.equal(conversationTransferSchema.safeParse({ messages: invalid }).success, false);
});
