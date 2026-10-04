import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { createAnswerDecoder } from '../src/lib/streamed-answer';
import { readSearchResponse, updateStreamingMessage, type SearchResponse, type SearchStreamEvent } from '../src/lib/search-stream';
import { POST } from '../src/app/api/search/route';
import * as store from '../src/lib/store';

const temporary = mkdtempSync(path.join(tmpdir(), 'garra-stream-tests-'));
process.env.GARRA_DATA_DIR = temporary;
process.env.DATA_ENCRYPTION_KEY = randomBytes(32).toString('hex');
process.env.LLM_PROVIDER = 'openai'; // these tests mock OpenAI's Responses SSE
process.env.OPENAI_API_KEY = 'test-key';
after(() => { store.db().close(); rmSync(temporary, { recursive: true, force: true }); });
const encoder = new TextEncoder();
const result: SearchResponse = { answer: 'Réponse 🐟', region: 'body', sources: [], diseases: [], steps: [], mode: 'ai', messages: [{ id: 'answer', role: 'assistant', text: 'Réponse 🐟' }] };
const completed = (text: string) => ({ type: 'response.completed', response: { status: 'completed', output: [{ type: 'message', content: [{ type: 'output_text', text }] }] } });
const sse = (event: unknown) => encoder.encode(`data: ${JSON.stringify(event)}\r\n\r\n`);
const request = (accept = 'application/x-ndjson', query = 'hello world') => new Request('http://127.0.0.1:3000/api/search', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: accept }, body: JSON.stringify({ query, surface: 'landing' }) });
function response(events: SearchStreamEvent[], size = 1) {
  const bytes = encoder.encode(events.map(event => JSON.stringify(event)).join('\n'));
  return new Response(new ReadableStream({ start(controller) {
    for (let i = 0; i < bytes.length; i += size) controller.enqueue(bytes.slice(i, i + size));
    controller.close();
  } }), { headers: { 'Content-Type': 'application/x-ndjson' } });
}

test('structured answer decoder handles every chunk boundary without exposing metadata or JSON escapes', () => {
  const answer = '## Résumé 🐟\n\n- "Quoted" text\n- C:\\papers\tTabbed / done';
  for (const data of [
    JSON.stringify({ answer, region: 'body', nextSteps: ['PRIVATE METADATA'] }),
    JSON.stringify({ suggestion: { answer: 'NESTED' }, nextSteps: ['answer'], answer, region: 'body' }),
    JSON.stringify({ answer }).replace('🐟', '\\ud83d\\udc1f'),
  ]) {
    for (let size = 1; size <= data.length; size++) {
      const decode = createAnswerDecoder();
      let visible = '';
      for (let offset = 0; offset < data.length; offset += size) {
        visible += decode(data.slice(offset, offset + size));
        assert.ok(answer.startsWith(visible));
        assert.doesNotMatch(visible, /[\uD800-\uDBFF]$/);
      }
      assert.equal(visible, answer);
    }
  }
});

test('browser reader handles fragmented UTF-8, tool resets, and final metadata', async () => {
  const visible: string[] = [];
  const final = await readSearchResponse(response([
    { type: 'delta', delta: 'Draft' }, { type: 'reset' },
    { type: 'delta', delta: 'Réponse ' }, { type: 'delta', delta: '🐟' },
    { type: 'complete', result },
  ]), text => visible.push(text));
  assert.deepEqual(visible, ['Draft', '', 'Réponse ', 'Réponse 🐟']);
  assert.deepEqual(final, result);
});

test('browser reader rejects interrupted streams and streamed errors after partial text', async () => {
  for (const ending of [[], [{ type: 'error', error: 'This conversation was deleted.' } satisfies SearchStreamEvent]]) {
    const visible: string[] = [];
    await assert.rejects(readSearchResponse(response([{ type: 'delta', delta: 'Unfinished' }, ...ending]), text => visible.push(text)), /interrupted|deleted/);
    assert.deepEqual(visible, ['Unfinished']);
  }
  await assert.rejects(readSearchResponse(Response.json({ error: 'Please sign in.' }, { status: 401 }), () => {}), /sign in/);
  assert.deepEqual(await readSearchResponse(Response.json(result), () => {}), result);
});

test('stream updates keep one assistant bubble and resets remove only that draft', () => {
  const initial = [{ id: 'question', role: 'user' as const, text: 'Hello' }];
  let messages = updateStreamingMessage(initial, 'answer', 'First');
  messages = updateStreamingMessage(messages, 'answer', 'First part');
  assert.equal(messages.length, 2);
  assert.equal(messages[1].text, 'First part');
  assert.equal(messages[1].streaming, true);
  assert.deepEqual(updateStreamingMessage(messages, 'answer', ''), initial);
});

test('search route delivers real answer text before the provider completes, then finalizes metadata', async t => {
  const started = Promise.withResolvers<void>();
  let provider!: ReadableStreamDefaultController<Uint8Array>;
  t.mock.method(globalThis, 'fetch', async () => {
    const stream = new ReadableStream<Uint8Array>({ start(controller) { provider = controller; } });
    started.resolve();
    return new Response(stream);
  });
  const answer = 'First paragraph.\n\nSecond paragraph 🐟.';
  const structured = JSON.stringify({ answer, region: 'body', sourceIds: [], nextSteps: ['Read the sources.'], suggestion: null });
  const streamed = await POST(request('application/x-ndjson', 'Fabry disease'));
  assert.match(streamed.headers.get('content-type')!, /application\/x-ndjson/);
  assert.match(streamed.headers.get('cache-control')!, /no-transform/);
  const firstText = Promise.withResolvers<string>();
  const visible: string[] = [];
  let finished = false;
  const reading = readSearchResponse(streamed, text => { visible.push(text); firstText.resolve(text); }).then(data => { finished = true; return data; });
  await started.promise;
  const prefix = '{"answer":"First paragraph.';
  provider.enqueue(sse({ type: 'response.output_text.delta', delta: prefix }));
  assert.equal(await firstText.promise, 'First paragraph.');
  assert.equal(finished, false, 'text must be visible while the provider is still generating');
  provider.enqueue(sse({ type: 'response.output_text.delta', delta: structured.slice(prefix.length) }));
  provider.enqueue(sse(completed(structured)));
  provider.close();
  const final = await reading;
  assert.equal(final.mode, 'ai');
  assert.equal(final.answer, answer);
  assert.equal(visible.at(-1), answer);
  assert.deepEqual(final.steps, ['Read the sources.']);
  assert.equal(final.messages.at(-1)?.streaming, undefined);
  assert.deepEqual(final.messages.at(-1)?.communities?.map(community => community.id), ['fabry']);
});

test('a provider failure replaces the partial answer with the existing database fallback', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({ start(controller) {
    controller.enqueue(sse({ type: 'response.output_text.delta', delta: '{"answer":"Partial answer' }));
    controller.enqueue(sse({ type: 'response.incomplete' }));
    controller.close();
  } })));
  const visible: string[] = [];
  const final = await readSearchResponse(await POST(request()), text => visible.push(text));
  assert.deepEqual(visible, ['Partial answer']);
  assert.equal(final.mode, 'database');
  assert.notEqual(final.answer, visible.at(-1));
  assert.match(final.warning!, /did not finish/);
});

test('cancelling the browser stream aborts the provider request', async t => {
  const started = Promise.withResolvers<AbortSignal>();
  const aborted = Promise.withResolvers<void>();
  t.mock.method(globalThis, 'fetch', async (_url: string, options: RequestInit) => {
    const signal = options.signal!;
    const stream = new ReadableStream<Uint8Array>({ start(controller) {
      signal.addEventListener('abort', () => { controller.error(signal.reason); aborted.resolve(); }, { once: true });
    } });
    started.resolve(signal);
    return new Response(stream);
  });
  const streamed = await POST(request());
  const signal = await started.promise;
  await streamed.body!.cancel();
  await aborted.promise;
  assert.equal(signal.aborted, true);
});

test('invalid requests still return regular HTTP errors before streaming starts', async () => {
  const invalid = await POST(request('application/x-ndjson', ''));
  assert.equal(invalid.status, 400);
  assert.match(invalid.headers.get('content-type')!, /application\/json/);
});

test('clients that do not request a stream still receive the completed JSON result', async t => {
  const text = JSON.stringify({ answer: 'Complete answer.', region: 'body', sourceIds: [], nextSteps: [], suggestion: null });
  t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({ start(controller) { controller.enqueue(sse(completed(text))); controller.close(); } })));
  const ordinary = await POST(request('application/json'));
  assert.match(ordinary.headers.get('content-type')!, /application\/json/);
  assert.equal((await ordinary.json()).answer, 'Complete answer.');
});
