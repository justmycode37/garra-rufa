import test from 'node:test';
import assert from 'node:assert/strict';
import { llmModel, llmResponse } from '../src/lib/llm';

delete process.env.LLM_PROVIDER; // OpenAI is the default
process.env.OPENAI_API_KEY = 'openai-test';
process.env.OPENROUTER_API_KEY = 'openrouter-test';

const chatSse = (text: string) => new Response(
  [{ choices: [{ delta: { content: text } }] }, { choices: [{ delta: {}, finish_reason: 'stop' }] }].map(e => `data: ${JSON.stringify(e)}\n\n`).join('') + 'data: [DONE]\n\n',
  { headers: { 'Content-Type': 'text/event-stream' } });

test('other OpenAI failures do not fall back', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ error: { code: 'server_error' } }, { status: 500 }));
  await assert.rejects(llmResponse({ model: 'gpt-6-luna', instructions: '', input: [] }), { code: 'request_failed', status: 500 });
  assert.equal((globalThis.fetch as any).mock.callCount(), 1);
});

test('an OpenAI account without credits falls back to the OpenRouter free router', async t => {
  const calls: { url: string; body: any }[] = [];
  t.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    calls.push({ url, body: JSON.parse(String(init.body)) });
    if (url.includes('api.openai.com')) return Response.json({ error: { code: 'insufficient_quota', type: 'insufficient_quota' } }, { status: 429 });
    return chatSse('hello');
  });
  assert.equal(llmModel('landing'), 'gpt-6-luna');
  const response = await llmResponse({ model: 'gpt-6-luna', instructions: 'be brief', input: [{ role: 'user', content: 'hi' }] });
  assert.deepEqual(response.output, [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'hello' }] }]);
  assert.deepEqual(calls.map(c => [new URL(c.url).host, c.body.model]), [['api.openai.com', 'gpt-6-luna'], ['openrouter.ai', 'openrouter/free']]);

  // While the quota is exhausted, requests skip OpenAI.
  assert.equal(llmModel('landing'), 'openrouter/free');
  await llmResponse({ model: 'gpt-6-luna', instructions: 'be brief', input: [{ role: 'user', content: 'again' }] });
  assert.equal(new URL(calls.at(-1)!.url).host, 'openrouter.ai');
  assert.equal(calls.length, 3);
});
