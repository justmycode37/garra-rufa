import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes, createHash } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT } from 'jose';
import * as store from '../src/lib/store';
import * as accounts from '../src/lib/chatgpt-store';
import { callbackClientId, chatGPTAccess, CHATGPT_ISSUER, disconnectChatGPT, finishChatGPTSignIn, localChatGPTOrigin, readTokens, startChatGPTSignIn, verifyChatGPTIdentity } from '../src/lib/chatgpt';
import { ChatGPTInferenceError, chatGPTResponse, readCompletedResponse, selectChatGPTModel, subscriptionRequest } from '../src/lib/chatgpt-inference';
import { answerQuery } from '../src/lib/ai';

const temporary = mkdtempSync(path.join(tmpdir(), 'garra-chatgpt-tests-'));
process.env.GARRA_DATA_DIR = temporary;
process.env.DATA_ENCRYPTION_KEY = randomBytes(32).toString('hex');
process.env.OPENAI_API_KEY = 'must-never-be-used';
let keyPair: Awaited<ReturnType<typeof generateKeyPair>>;
let jwk: Awaited<ReturnType<typeof exportJWK>>;
let keys: ReturnType<typeof createLocalJWKSet>;
before(async () => { keyPair = await generateKeyPair('RS256'); jwk = { ...await exportJWK(keyPair.publicKey), kid: 'test-key' }; keys = createLocalJWKSet({ keys: [jwk] }); });
after(() => { store.db().close(); rmSync(temporary, { recursive: true, force: true }); });
const json = (value: unknown, status = 200) => Response.json(value, { status });
const config = { issuer: CHATGPT_ISSUER, authorization_endpoint: `${CHATGPT_ISSUER}/api/accounts/authorize`, token_endpoint: `${CHATGPT_ISSUER}/api/accounts/oauth/token`, jwks_uri: `${CHATGPT_ISSUER}/.well-known/jwks.json`, revocation_endpoint: `${CHATGPT_ISSUER}/oauth/revoke` };
const attempt = (overrides: Partial<accounts.SignInAttempt> = {}): accounts.SignInAttempt => ({ state: 'state', nonce: 'nonce', verifier: 'verifier', redirectUri: 'http://127.0.0.1:3000/api/auth/chatgpt/callback', role: 'researcher', browserId: 'browser', clientId: 'dynamic_agent_client', ...overrides });
const tokens = (overrides: Partial<accounts.ChatGPTTokens> = {}): accounts.ChatGPTTokens => ({ accessToken: 'access-secret', refreshToken: 'refresh-secret', idToken: 'id-secret', expiresAt: Date.now() + 3600000, scopes: ['openid', 'chatgpt.tokens.use.direct'], ...overrides });
const connect = (clientId: string, options: Partial<accounts.SignInAttempt> = {}, tokenSet = tokens()) => accounts.connectAccount({ issuer: CHATGPT_ISSUER, subject: 'subject', clientId, name: 'Test Person', email: 'same@example.test' }, tokenSet, attempt(options));
async function signed(claims: Record<string, unknown> = {}) {
  return new SignJWT({ nonce: 'nonce', name: 'Test Person', email: 'same@example.test', ...claims }).setProtectedHeader({ alg: 'RS256', kid: 'test-key' }).setIssuer(CHATGPT_ISSUER).setAudience('oaiapp_test').setSubject('subject').setIssuedAt().setExpirationTime('1h').sign(keyPair.privateKey);
}
function sse(events: unknown[], chunkSize = 11) {
  const bytes = new TextEncoder().encode(events.map(event => `data: ${JSON.stringify(event)}\r\n\r\n`).join(''));
  return new Response(new ReadableStream({ start(controller) { for (let i = 0; i < bytes.length; i += chunkSize) controller.enqueue(bytes.slice(i, i + chunkSize)); controller.close(); } }), { headers: { 'Content-Type': 'text/event-stream', 'x-request-id': 'test-request' } });
}
const completed = (output: unknown[] = []) => ({ type: 'response.completed', response: { status: 'completed', output } });
const itemDone = (item: unknown, output_index = 0) => ({ type: 'response.output_item.done', output_index, item });

test('local OAuth cannot be enabled on a hosted URL or a mismatched canonical origin', () => {
  assert.equal(localChatGPTOrigin('http://127.0.0.1:3000/api/auth/chatgpt', ''), 'http://127.0.0.1:3000');
  for (const url of ['https://example.com', 'http://localhost:3000', 'http://127.0.0.1.example.com', 'https://127.0.0.1:3000']) assert.throws(() => localChatGPTOrigin(url, ''));
  assert.throws(() => localChatGPTOrigin('http://127.0.0.1:3000', 'https://example.com'));
});

test('OAuth attempts are encrypted, browser-bound, expiring and consumed once', () => {
  const original = attempt();
  const id = accounts.saveAttempt(original);
  const row = store.db().prepare('SELECT * FROM chatgpt_attempts').get() as { id: string; payload: string };
  assert.notEqual(row.id, id); assert.ok(!row.payload.includes('verifier'));
  assert.deepEqual(accounts.consumeAttempt(id, 'state'), original);
  assert.throws(() => accounts.consumeAttempt(id, 'state'));
  const mismatch = accounts.saveAttempt(original);
  assert.throws(() => accounts.consumeAttempt(mismatch, 'wrong'));
  assert.throws(() => accounts.consumeAttempt(mismatch, 'state'));
  const expired = accounts.saveAttempt(original);
  store.db().prepare('UPDATE chatgpt_attempts SET expires=0').run();
  assert.throws(() => accounts.consumeAttempt(expired, 'state'));
});

test('authorization uses PKCE, fresh state and nonce, a stable host, and complete scopes', async t => {
  t.mock.method(globalThis, 'fetch', async () => json(config));
  const first = await startChatGPTSignIn({ requestUrl: 'http://127.0.0.1:3000', browserId: 'browser', role: 'doctor', current: null });
  const second = await startChatGPTSignIn({ requestUrl: 'http://127.0.0.1:3000', browserId: 'browser', role: 'doctor', current: null });
  const url = new URL(first.url), other = new URL(second.url);
  const saved = accounts.consumeAttempt(first.attemptId, url.searchParams.get('state'));
  assert.equal(url.origin, CHATGPT_ISSUER);
  assert.equal(url.searchParams.get('agent_name_hint'), 'Garra Rufa');
  assert.equal(url.searchParams.get('client_id'), 'dynamic_agent_client');
  assert.equal(url.searchParams.get('ext_agent_host_id'), other.searchParams.get('ext_agent_host_id'));
  assert.notEqual(url.searchParams.get('state'), other.searchParams.get('state'));
  assert.notEqual(url.searchParams.get('nonce'), other.searchParams.get('nonce'));
  assert.equal(url.searchParams.get('code_challenge'), createHash('sha256').update(saved.verifier).digest('base64url'));
  assert.match(url.searchParams.get('scope')!, /chatgpt.tokens.use.direct/);
  assert.equal(url.searchParams.get('redirect_uri'), saved.redirectUri);
});

test('issued client IDs are required for registration and fixed for reauthorization', () => {
  assert.equal(callbackClientId(attempt(), 'oaiapp_new'), 'oaiapp_new');
  assert.throws(() => callbackClientId(attempt(), null));
  assert.throws(() => callbackClientId(attempt(), 'dynamic_agent_client'));
  assert.equal(callbackClientId(attempt({ clientId: 'oaiapp_old' }), null), 'oaiapp_old');
  assert.throws(() => callbackClientId(attempt({ clientId: 'oaiapp_old' }), 'oaiapp_other'));
});

test('ID token verification rejects forged, expired, wrong-issuer, audience and nonce tokens', async () => {
  const valid = await signed();
  assert.equal((await verifyChatGPTIdentity(valid, 'oaiapp_test', 'nonce', keys)).subject, 'subject');
  await assert.rejects(verifyChatGPTIdentity(valid, 'wrong-client', 'nonce', keys));
  await assert.rejects(verifyChatGPTIdentity(valid, 'oaiapp_test', 'wrong-nonce', keys));
  const wrongIssuer = await new SignJWT({ nonce: 'nonce' }).setProtectedHeader({ alg: 'RS256', kid: 'test-key' }).setIssuer('https://fake.example').setAudience('oaiapp_test').setSubject('subject').setIssuedAt().setExpirationTime('1h').sign(keyPair.privateKey);
  await assert.rejects(verifyChatGPTIdentity(wrongIssuer, 'oaiapp_test', 'nonce', keys));
  const expired = await new SignJWT({ nonce: 'nonce' }).setProtectedHeader({ alg: 'RS256', kid: 'test-key' }).setIssuer(CHATGPT_ISSUER).setAudience('oaiapp_test').setSubject('subject').setIssuedAt().setExpirationTime(1).sign(keyPair.privateKey);
  await assert.rejects(verifyChatGPTIdentity(expired, 'oaiapp_test', 'nonce', keys));
  const pieces = valid.split('.'); pieces[1] = Buffer.from(JSON.stringify({ sub: 'attacker' })).toString('base64url');
  await assert.rejects(verifyChatGPTIdentity(pieces.join('.'), 'oaiapp_test', 'nonce', keys));
});

test('a valid identity without plan permission remains signed in but cannot perform inference', async () => {
  const identityOnly = readTokens({ id_token: await signed(), scope: 'openid email profile' });
  const account = connect('oaiapp_identity', {}, identityOnly);
  assert.equal(accounts.planEnabled(account), false);
  assert.equal(accounts.withChatGPT(store.sessionUser(store.createSession(account.userId)))?.chatgpt?.planEnabled, false);
  await assert.rejects(chatGPTAccess(account.userId), /not enabled/);
  assert.throws(() => readTokens({ id_token: 'id', scope: 'chatgpt.tokens.use.direct' }));
});

test('same emails across registrations never merge workspaces; saved account lists are browser-scoped', () => {
  const first = connect('oaiapp_first', { browserId: 'first-browser' });
  const second = connect('oaiapp_second', { browserId: 'second-browser' });
  assert.notEqual(first.userId, second.userId);
  const record = store.saveRecord(first.userId, { kind: 'note', title: 'Private', content: 'Private evidence' });
  assert.equal(store.getRecord(record.id, second.userId), null);
  assert.deepEqual(accounts.browserAccounts('first-browser').map(a => a.id), [first.id]);
  assert.deepEqual(accounts.browserAccounts('unknown-browser'), []);
  const encrypted = store.db().prepare('SELECT payload FROM chatgpt_accounts WHERE id=?').get(first.id) as { payload: string };
  assert.ok(!encrypted.payload.includes('access-secret')); assert.ok(!encrypted.payload.includes('same@example.test'));
  const unchanged = accounts.accountById(first.id);
  assert.throws(() => accounts.connectAccount({ issuer: CHATGPT_ISSUER, subject: 'attacker', clientId: first.clientId, name: 'Other', email: first.email }, tokens(), attempt({ accountId: first.id, clientId: first.clientId })));
  assert.deepEqual(accounts.accountById(first.id), unchanged);
});

test('a new connection preserves its guest records; returning accounts do not import another guest workspace', () => {
  const guest = store.createGuest('patient');
  const record = store.saveRecord(guest.id, { kind: 'note', title: 'Guest note' });
  const linked = connect('oaiapp_guest', { guestUserId: guest.id, currentUserId: guest.id, role: 'patient' });
  assert.equal(linked.userId, guest.id);
  assert.equal(store.sessionUser(store.createSession(guest.id))?.guest, false);
  assert.ok(store.getRecord(record.id, linked.userId));
  const other = store.createGuest('doctor');
  const otherRecord = store.saveRecord(other.id, { kind: 'note', title: 'Keep separate' });
  const returning = connect('oaiapp_guest', { guestUserId: other.id, accountId: linked.id, clientId: linked.clientId });
  assert.equal(returning.userId, linked.userId);
  assert.equal(store.getRecord(otherRecord.id, returning.userId), null);
});

test('returning authorization reuses the issued registration; unowned registrations are rejected', async t => {
  t.mock.method(globalThis, 'fetch', async () => json(config));
  const account = connect('oaiapp_return', { browserId: 'return-browser' });
  const result = await startChatGPTSignIn({ requestUrl: 'http://127.0.0.1:3000', browserId: 'return-browser', role: 'researcher', current: null, accountId: account.id });
  const url = new URL(result.url);
  assert.equal(url.searchParams.get('client_id'), account.clientId);
  assert.equal(url.searchParams.get('id_token_hint'), 'id-secret');
  assert.equal(url.searchParams.has('agent_name_hint'), false);
  await assert.rejects(startChatGPTSignIn({ requestUrl: 'http://127.0.0.1:3000', browserId: 'stranger', role: 'doctor', current: null, accountId: account.id }));
});

test('the code exchange validates identity and uses the original callback and verifier without a secret', async t => {
  const idToken = await signed();
  let exchange: URLSearchParams | undefined;
  t.mock.method(globalThis, 'fetch', async (url: string, init?: RequestInit) => {
    if (String(url).includes('jwks')) return json({ keys: [jwk] });
    exchange = init?.body as URLSearchParams;
    return json({ access_token: 'new-access', refresh_token: 'new-refresh', id_token: idToken, token_type: 'Bearer', expires_in: 3600, scope: 'openid chatgpt.tokens.use.direct' });
  });
  const saved = attempt();
  const result = await finishChatGPTSignIn(saved, new URL(`${saved.redirectUri}?code=test-code&client_id=oaiapp_test`));
  assert.equal(result.subject, 'subject');
  assert.equal(exchange?.get('client_id'), 'oaiapp_test');
  assert.equal(exchange?.get('redirect_uri'), saved.redirectUri);
  assert.equal(exchange?.get('code_verifier'), saved.verifier);
  assert.equal(exchange?.has('client_secret'), false);
  await assert.rejects(finishChatGPTSignIn(saved, new URL(`${saved.redirectUri}?error=access_denied`)));
});

test('refreshes serialize and atomically rotate tokens, retaining scopes when omitted', async t => {
  const account = connect('oaiapp_refresh', {}, tokens({ expiresAt: 1 }));
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async (_url: string, init: RequestInit) => {
    calls++; assert.equal((init.body as URLSearchParams).get('refresh_token'), 'refresh-secret');
    assert.equal((init.body as URLSearchParams).has('scope'), false);
    return json({ access_token: 'rotated-access', refresh_token: 'rotated-refresh', token_type: 'Bearer', expires_in: 3600 });
  });
  const [a, b] = await Promise.all([chatGPTAccess(account.userId), chatGPTAccess(account.userId)]);
  assert.equal(calls, 1); assert.equal(a.accessToken, 'rotated-access'); assert.deepEqual(a, b);
  assert.equal(accounts.accountById(account.id)?.tokens?.refreshToken, 'rotated-refresh');
});

test('temporary refresh failures preserve credentials; terminal revocation clears them', async t => {
  const transient = connect('oaiapp_transient', {}, tokens({ expiresAt: 1 }));
  const terminal = connect('oaiapp_terminal', {}, tokens({ expiresAt: 1 }));
  t.mock.method(globalThis, 'fetch', async (_url: string, init: RequestInit) => (init.body as URLSearchParams).get('client_id') === transient.clientId ? json({ error: 'temporarily_unavailable' }, 503) : json({ error: 'invalid_grant' }, 400));
  await assert.rejects(chatGPTAccess(transient.userId)); assert.ok(accounts.accountById(transient.id)?.tokens);
  await assert.rejects(chatGPTAccess(terminal.userId), /expired/); assert.equal(accounts.accountById(terminal.id)?.tokens, undefined);
});

test('sign-out revokes the renewable session, clears tokens and keeps the registration for later', async t => {
  const account = connect('oaiapp_logout');
  t.mock.method(globalThis, 'fetch', async (_url: string, init: RequestInit) => {
    const body = init.body as URLSearchParams;
    assert.equal(body.get('token_type_hint'), 'refresh_token'); assert.equal(body.get('client_id'), account.clientId);
    return new Response(null, { status: 200 });
  });
  assert.equal(await disconnectChatGPT(account.userId), true);
  assert.equal(accounts.accountById(account.id)?.tokens, undefined);
  assert.equal(accounts.accountById(account.id)?.clientId, account.clientId);
  assert.throws(() => accounts.saveAccount({ ...account, tokens: tokens() }), /connection changed/);
  await assert.rejects(chatGPTAccess(account.userId));
});

test('SSE accepts fragmented UTF-8/CRLF only after response.completed', async () => {
  const output = [{ type: 'message', content: [{ type: 'output_text', text: 'Réponse 🐟' }] }];
  assert.deepEqual((await readCompletedResponse(sse([{ type: 'response.output_text.delta', delta: 'partial' }, completed(output)], 1))).output, output);
  await assert.rejects(readCompletedResponse(sse([{ type: 'response.output_text.delta', delta: 'partial' }])), /did not finish/);
  await assert.rejects(readCompletedResponse(sse([{ type: 'response.incomplete' }])), /did not finish/);
});

test('SSE retains completed output items when the terminal event has empty output', async () => {
  const reasoning = { id: 'reasoning1', type: 'reasoning', encrypted_content: 'encrypted-test-reasoning', summary: [] };
  const message = { id: 'message1', type: 'message', role: 'assistant', status: 'completed', content: [{ type: 'output_text', text: 'Réponse 🐟' }] };
  const response = await readCompletedResponse(sse([
    { type: 'response.output_item.added', output_index: 1, item: { ...message, status: 'in_progress', content: [] } },
    { type: 'response.output_text.delta', output_index: 1, content_index: 0, delta: 'Réponse 🐟' },
    itemDone(message, 1), itemDone(reasoning, 0), itemDone(message, 1), completed(),
  ], 1));
  assert.deepEqual(response.output, [reasoning, message]);
});

test('SSE prefers the populated terminal output without duplicating streamed items', async () => {
  const message = { type: 'message', content: [{ type: 'output_text', text: 'Final answer' }] };
  assert.deepEqual((await readCompletedResponse(sse([itemDone(message), completed([message])]))).output, [message]);
});

test('completed output items do not turn a failed or unfinished stream into an answer', async () => {
  const message = { type: 'message', content: [{ type: 'output_text', text: 'Do not return this' }] };
  await assert.rejects(readCompletedResponse(sse([itemDone(message)])), /did not finish/);
  await assert.rejects(readCompletedResponse(sse([itemDone(message), { type: 'response.incomplete' }])), /did not finish/);
  await assert.rejects(readCompletedResponse(sse([itemDone(message), { type: 'response.failed', response: { error: { code: 'subscription_sharing_usage_limit_exceeded' } } }])), error => error instanceof ChatGPTInferenceError && error.code === 'subscription_sharing_usage_limit_exceeded');
});

test('usage failures after stream start are failures; nonstandard admission bodies are handled without exposing them', async () => {
  await assert.rejects(readCompletedResponse(sse([{ type: 'response.output_text.delta', delta: 'partial' }, { type: 'response.failed', response: { error: { code: 'subscription_sharing_usage_limit_exceeded' } } }])), error => error instanceof ChatGPTInferenceError && error.code === 'subscription_sharing_usage_limit_exceeded' && error.requestId === 'test-request');
  await assert.rejects(readCompletedResponse(json({ detail: 'secret diagnostic string' }, 403)), error => error instanceof Error && !error.message.includes('secret diagnostic'));
});

test('requests use subscription-compatible fields and the account-specific model catalog', async t => {
  t.mock.method(globalThis, 'fetch', async (_url: string, init: RequestInit) => { assert.equal((init.headers as Record<string, string>).Authorization, 'Bearer user-token'); return json({ models: [{ slug: 'hidden', display_name: 'Hidden', visibility: 'hide' }, { slug: 'available', display_name: 'Available', visibility: 'list' }] }); });
  assert.equal(await selectChatGPTModel('user-token', 'not-in-account'), 'available');
  const body = subscriptionRequest({ model: 'available', instructions: 'Help', input: [{ role: 'user', content: 'Hi' }], format: { type: 'json_object' }, tools: [{ type: 'function', name: 'read_evidence' }] });
  assert.equal(body.stream, true); assert.equal(body.store, false); assert.equal(body.tools?.[0].type, 'namespace');
  for (const field of ['max_output_tokens', 'previous_response_id', 'temperature', 'conversation']) assert.equal(field in body, false);
});

test('anonymous and disconnected users cannot spend an API key, and plan errors never fall back to API billing', async t => {
  let requests: string[] = [];
  t.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    requests.push(String(url));
    assert.equal((init.headers as Record<string, string>).Authorization, 'Bearer access-secret');
    if (String(url).endsWith('/models')) return json({ models: [{ slug: 'available', display_name: 'Available', visibility: 'list' }] });
    return sse([{ type: 'response.failed', response: { error: { code: 'subscription_sharing_usage_limit_exceeded' } } }]);
  });
  const input = { query: 'hello world', history: [], fileIds: [], includeWorkspace: false, chatGPTAllowed: true, billing: 'chatgpt' as const };
  assert.equal((await answerQuery({ ...input, user: null })).mode, 'database'); assert.equal(requests.length, 0);
  const guest = store.createGuest('researcher');
  assert.equal((await answerQuery({ ...input, user: guest })).mode, 'database'); assert.equal(requests.length, 0);
  const account = connect('oaiapp_billing');
  const user = accounts.withChatGPT(store.sessionUser(store.createSession(account.userId)))!;
  const result = await answerQuery({ ...input, user });
  assert.equal(result.mode, 'database'); assert.match(result.warning!, /usage limit/);
  assert.deepEqual(requests, ['https://api.openai.com/v1/models', 'https://api.openai.com/v1/responses']);
  requests = [];
  assert.equal((await answerQuery({ ...input, user, chatGPTAllowed: false })).mode, 'database'); assert.equal(requests.length, 0);
});

test('successful subscription answers complete the tool loop and preserve evidence sources', async t => {
  const account = connect('oaiapp_answer');
  let round = 0;
  t.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    if (String(url).endsWith('/models')) return json({ models: [{ slug: 'available', display_name: 'Available', visibility: 'list' }] });
    const body = JSON.parse(init.body as string);
    assert.equal(body.stream, true); assert.equal(body.store, false);
    if (++round === 1) return sse([itemDone({ type: 'function_call', namespace: 'garra', name: 'search_knowledge', call_id: 'call1', arguments: JSON.stringify({ query: 'Marfan' }) }), completed()]);
    assert.ok(body.input.some((item: { type?: string }) => item.type === 'function_call_output'));
    return sse([itemDone({ type: 'message', content: [{ type: 'output_text', text: JSON.stringify({ answer: 'Evidence summary [1].', region: 'heart', sourceIds: ['marfan'], nextSteps: [], suggestion: null }) }] }), completed()]);
  });
  const user = accounts.withChatGPT(store.sessionUser(store.createSession(account.userId)))!;
  const result = await answerQuery({ query: 'hello world', user, history: [], fileIds: [], includeWorkspace: false, chatGPTAllowed: true, billing: 'chatgpt' as const });
  assert.equal(result.mode, 'ai'); assert.equal(round, 2); assert.equal(result.sources[0].id, 'marfan');
});

test('confirmed invalid subscriber context stops future use, while transport failures do not erase tokens', async t => {
  const account = connect('oaiapp_revoked');
  t.mock.method(globalThis, 'fetch', async () => json({ error: { code: 'subscription_sharing_invalid_user' } }, 401));
  await assert.rejects(chatGPTResponse(account.userId, subscriptionRequest({ model: 'x', input: [], instructions: '', format: {} })));
  assert.equal(accounts.accountById(account.id)?.tokens, undefined);
});

test('landing AI uses the app API key, never the subscription token or workspace records', async t => {
  let requests = 0;
  t.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    requests++;
    assert.equal(String(url), 'https://api.openai.com/v1/responses');
    assert.equal((init.headers as Record<string, string>).Authorization, 'Bearer must-never-be-used');
    const body = JSON.parse(init.body as string);
    assert.equal(body.model, process.env.OPENAI_SEARCH_MODEL || 'gpt-6-luna');
    assert.ok(!JSON.stringify(body.tools).includes('search_workspace'));
    return sse([itemDone({ type: 'message', content: [{ type: 'output_text', text: JSON.stringify({ answer: 'Public answer.', region: 'body', sourceIds: [], nextSteps: [], suggestion: null }) }] }), completed()]);
  });
  const result = await answerQuery({ query: 'hello world', user: null, history: [], fileIds: [], includeWorkspace: false, chatGPTAllowed: false, billing: 'api' });
  assert.equal(result.mode, 'ai'); assert.equal(requests, 1);
});

test('workspace authentication rejects anonymous, guest and password-only sessions', () => {
  assert.equal(accounts.workspaceUser(''), null);
  const guest = store.createGuest('doctor');
  assert.equal(accounts.workspaceUser(store.createSession(guest.id)), null);
  const legacy = store.createUser('Legacy', 'legacy@example.test', 'long-legacy-password', 'patient');
  assert.equal(accounts.workspaceUser(store.createSession(legacy.id)), null);
  const account = connect('oaiapp_workspace');
  const session = store.createSession(account.userId);
  assert.equal(accounts.workspaceUser(session)?.id, account.userId);
  store.removeSession(session);
  assert.equal(accounts.workspaceUser(session), null);
});

test('verified email supports explicit sharing but never links an existing account by email', () => {
  const identity = { issuer: CHATGPT_ISSUER, subject: 'verified-subject', clientId: 'oaiapp_verified_email', name: 'Verified Person', email: 'verified@example.test', emailVerified: true };
  const account = accounts.connectAccount(identity, tokens(), attempt());
  const owner = store.createUser('Sharing owner', 'sharing-owner@example.test', 'long-sharing-password', 'researcher');
  const record = store.saveRecord(owner.id, { kind: 'note', title: 'Explicitly shared' });
  store.shareRecord(record.id, owner.id, identity.email);
  assert.ok(store.getRecord(record.id, account.userId)?.readOnly);
  const separate = accounts.connectAccount({ ...identity, clientId: 'oaiapp_other_verified' }, tokens(), attempt());
  assert.notEqual(separate.userId, account.userId);
  assert.equal(store.getRecord(record.id, separate.userId), null);
});
