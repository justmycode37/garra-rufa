/**
 * LLM provider seam for all AI answers (landing + workspace).
 *
 * ACTIVE PROVIDER: OpenRouter (OpenAI-compatible chat completions), mirroring
 * src/query-test/evidence/llm.py. Env: OPENROUTER_API_KEY, OPENROUTER_MODEL
 * (default stealth/space-bunny-alpha). `LLM_PROVIDER` selects the backend.
 *
 * HOW TO SWITCH BACK TO THE OPENAI API (for a future agent):
 *   Fastest: set `LLM_PROVIDER=openai` in .env.local / Vercel. The OpenAI path is
 *   untouched: it still uses ./openai-response.ts (`openAIResponse`) with
 *   OPENAI_API_KEY, OPENAI_SEARCH_MODEL and OPENAI_WORKSPACE_MODEL.
 *   Permanent: change `DEFAULT_PROVIDER` below to 'openai', then optionally delete
 *   the OpenRouter section of this file. Nothing else in the app needs to change:
 *   ai.ts only calls `llmConfigured`, `llmModel` and `llmResponse` from here.
 *   Tests that mock OpenAI's Responses SSE set LLM_PROVIDER=openai themselves.
 *
 * Contract: `llmResponse` takes the OpenAI Responses-style body built by
 * `apiRequest()` and returns a CompletedResponse whose `output` holds `message`
 * (content: [{type:'output_text', text}]) and `function_call` items. The OpenRouter
 * adapter translates to/from chat completions so ai.ts stays provider-neutral.
 */
import { OpenAIResponseError, openAIResponse, type CompletedResponse, type ResponseStreamOptions } from './openai-response';

const DEFAULT_PROVIDER: 'openrouter' | 'openai' = 'openrouter';
const OPENROUTER_API = 'https://openrouter.ai/api/v1/chat/completions';
const OPENROUTER_DEFAULT_MODEL = 'stealth/space-bunny-alpha';

export const llmProvider = () => (process.env.LLM_PROVIDER || DEFAULT_PROVIDER).trim().toLowerCase() === 'openai' ? 'openai' : 'openrouter';
export const llmConfigured = () => !!(llmProvider() === 'openai' ? process.env.OPENAI_API_KEY : process.env.OPENROUTER_API_KEY);
export const llmLabel = () => llmProvider() === 'openai' ? 'OpenAI' : 'OpenRouter';
export function llmModel(surface: 'landing' | 'workspace') {
  if (llmProvider() === 'openai') return surface === 'workspace' ? process.env.OPENAI_WORKSPACE_MODEL || 'gpt-6-astra' : process.env.OPENAI_SEARCH_MODEL || 'gpt-6-luna';
  return (process.env.OPENROUTER_MODEL || OPENROUTER_DEFAULT_MODEL).trim();
}

export function llmResponse(body: Record<string, unknown>, options: ResponseStreamOptions = {}): Promise<CompletedResponse> {
  return llmProvider() === 'openai' ? openAIResponse(body, options) : openRouterResponse(body, options);
}

// ---- OpenRouter adapter (Responses-style request -> chat completions) ----

type Item = Record<string, any>;
const effortMap: Record<string, string> = { low: 'low', medium: 'medium', high: 'high', max: 'high' };

function toPart(part: Item) {
  if (part.type === 'input_text') return { type: 'text', text: part.text };
  if (part.type === 'input_image') return { type: 'image_url', image_url: { url: part.image_url } };
  if (part.type === 'input_file') return { type: 'file', file: { filename: part.filename, file_data: part.file_data } };
  return { type: 'text', text: String(part.text ?? '') };
}

export function toChatMessages(instructions: string, input: Item[]) {
  const messages: Item[] = [{ role: 'system', content: instructions }];
  for (const item of input) {
    if (item.type === 'function_call') {
      const call = { id: item.call_id, type: 'function', function: { name: item.name, arguments: item.arguments } };
      const last = messages.at(-1);
      if (last?.role === 'assistant' && last.tool_calls) last.tool_calls.push(call);
      else if (last?.role === 'assistant' && !last.tool_calls) last.tool_calls = [call];
      else messages.push({ role: 'assistant', content: null, tool_calls: [call] });
    } else if (item.type === 'function_call_output') {
      messages.push({ role: 'tool', tool_call_id: item.call_id, content: String(item.output) });
    } else if (item.type === 'message' || item.role) {
      const content = Array.isArray(item.content) ? item.content : item.content;
      if (item.role === 'user') messages.push({ role: 'user', content: Array.isArray(content) ? content.map(toPart) : content });
      else messages.push({ role: 'assistant', content: Array.isArray(content) ? content.filter((p: Item) => p.type === 'output_text').map((p: Item) => p.text).join('') : content });
    }
    // Reasoning items (OpenAI-encrypted) have no chat-completions equivalent and are dropped.
  }
  return messages;
}

async function openRouterResponse(body: Record<string, unknown>, options: ResponseStreamOptions = {}): Promise<CompletedResponse> {
  const key = process.env.OPENROUTER_API_KEY;
  if (!key) throw new OpenAIResponseError('not_configured');
  const tools = ((body.tools as Item[] | undefined) ?? []).flatMap(t => t.type === 'namespace' ? t.tools : [t]) as Item[];
  const format = (body.text as Item | undefined)?.format as Item | undefined;
  const effort = (body.reasoning as Item | undefined)?.effort as string | undefined;
  const request = {
    model: body.model, messages: toChatMessages(String(body.instructions ?? ''), (body.input as Item[]) ?? []), stream: true,
    max_tokens: body.max_output_tokens,
    ...(tools.length ? { tools: tools.map(t => ({ type: 'function', function: { name: t.name, description: t.description, parameters: t.parameters, strict: t.strict } })) } : {}),
    ...(format ? { response_format: { type: 'json_schema', json_schema: { name: format.name, strict: format.strict, schema: format.schema } } } : {}),
    ...(effort ? { reasoning: { effort: effortMap[effort] ?? 'medium', exclude: true } } : {}),
  };
  const response = await fetch(OPENROUTER_API, { method: 'POST', cache: 'no-store', redirect: 'error',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}`, 'HTTP-Referer': 'https://garra-rufa.vercel.app', 'X-Title': 'garra-rufa' },
    body: JSON.stringify(request), signal: AbortSignal.any([AbortSignal.timeout(150000), ...(options.signal ? [options.signal] : [])]) });
  const requestId = response.headers.get('x-request-id') || undefined;
  if (!response.ok) throw new OpenAIResponseError('request_failed', response.status, requestId);
  if (!response.body) throw new OpenAIResponseError('incomplete_stream', undefined, requestId);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const calls = new Map<number, { id: string; name: string; arguments: string }>();
  let text = '', buffer = '', bytes = 0, finish: string | null = null, done = false;
  const handle = (block: string) => {
    const data = block.split(/\r?\n/).filter(l => l.startsWith('data:')).map(l => l.slice(5).trimStart()).join('\n');
    if (!data) return;
    if (data === '[DONE]') { done = true; return; }
    const event = JSON.parse(data);
    if (event.error) throw new OpenAIResponseError('request_failed', typeof event.error.code === 'number' ? event.error.code : undefined, requestId);
    const choice = event.choices?.[0];
    if (!choice) return;
    const delta = choice.delta ?? {};
    if (typeof delta.content === 'string' && delta.content) { text += delta.content; options.onTextDelta?.(delta.content); }
    for (const call of delta.tool_calls ?? []) {
      const current = calls.get(call.index ?? 0) ?? { id: '', name: '', arguments: '' };
      if (call.id) current.id = call.id;
      if (call.function?.name) current.name += call.function.name;
      if (call.function?.arguments) current.arguments += call.function.arguments;
      calls.set(call.index ?? 0, current);
    }
    if (choice.finish_reason) finish = choice.finish_reason;
  };
  try {
    while (!done) {
      const chunk = await reader.read();
      bytes += chunk.value?.byteLength || 0;
      if (bytes > 12 * 1024 * 1024) throw new OpenAIResponseError('incomplete_stream', undefined, requestId);
      buffer += decoder.decode(chunk.value, { stream: !chunk.done });
      let boundary: RegExpMatchArray | null;
      while ((boundary = buffer.match(/\r?\n\r?\n/))) {
        const block = buffer.slice(0, boundary.index!);
        buffer = buffer.slice(boundary.index! + boundary[0].length);
        handle(block);
      }
      if (chunk.done) { if (buffer.trim()) handle(buffer); break; }
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }

  // A reply cut off at max_tokens (reasoning included) or never finished is not an answer.
  if (!finish || finish === 'length' || finish === 'content_filter') throw new OpenAIResponseError('incomplete_stream', undefined, requestId);
  const output: Item[] = [];
  if (calls.size) {
    if (text) output.push({ type: 'message', role: 'assistant', content: [{ type: 'output_text', text }] });
    for (const [, c] of [...calls.entries()].sort(([a], [b]) => a - b)) output.push({ type: 'function_call', call_id: c.id, name: c.name, arguments: c.arguments });
  } else output.push({ type: 'message', role: 'assistant', content: [{ type: 'output_text', text }] });
  return { status: 'completed', output };
}
