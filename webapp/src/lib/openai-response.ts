import { z } from 'zod';
export class OpenAIResponseError extends Error {
  constructor(public code: string, public status?: number, public requestId?: string) {
    super(status === 429 ? 'The AI service has reached its usage limit. Please try again later.' : code === 'incomplete_stream' ? 'The AI response did not finish. Please try again.' : 'The AI service is temporarily unavailable. Please try again.');
  }
}
// OpenAI reports an account without credits as 429 `insufficient_quota` (HTTP body
// `{error:{code}}`, stream `response.failed` `{error:{code}}` or `error` `{code}`).
function inferenceError(body: unknown, status?: number, requestId?: string) {
  const b = (body ?? {}) as { code?: unknown; type?: unknown; error?: { code?: unknown; type?: unknown } };
  const quota = [b.code, b.type, b.error?.code, b.error?.type].includes('insufficient_quota');
  return new OpenAIResponseError(quota ? 'insufficient_quota' : 'request_failed', status ?? (quota ? 429 : undefined), requestId);
}
const responseSchema = z.object({ status: z.literal('completed'), output: z.array(z.record(z.string(), z.unknown())) }).passthrough();
const completedItemSchema = z.object({ output_index: z.number().int().nonnegative(), item: z.record(z.string(), z.unknown()) });
export type CompletedResponse = z.infer<typeof responseSchema>;
export type ResponseStreamOptions = { onTextDelta?: (delta: string) => void; signal?: AbortSignal };

export async function readCompletedResponse(response: Response, onTextDelta?: (delta: string) => void): Promise<CompletedResponse> {
  const requestId = response.headers.get('x-request-id') || response.headers.get('openai-request-id') || undefined;
  if (!response.ok) throw inferenceError(await response.json().catch(() => ({})), response.status, requestId);
  if (!response.body) throw new OpenAIResponseError('incomplete_stream');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const outputItems = new Map<number, CompletedResponse['output'][number]>();
  let buffer = '', bytes = 0;
  const parseEvent = (block: string) => {
    const data = block.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
    if (!data || data === '[DONE]') return null;
    const event = JSON.parse(data);
    if (event.type === 'response.failed') throw inferenceError(event.response, undefined, requestId);
    if (event.type === 'error') throw inferenceError(event, undefined, requestId);
    if (event.type === 'response.incomplete') throw new OpenAIResponseError('incomplete_stream', undefined, requestId);
    if (event.type === 'response.output_text.delta' && typeof event.delta === 'string') onTextDelta?.(event.delta);
    if (event.type === 'response.output_item.done') {
      const { output_index, item } = completedItemSchema.parse(event);
      outputItems.set(output_index, item);
    }
    if (event.type !== 'response.completed') return null;
    const completed = responseSchema.parse(event.response);
    // Some streams send the full items only in output_item.done, leaving the
    // terminal output empty. Preserve messages, tool calls and encrypted reasoning,
    // but only return authoritative output once the whole response completes.
    if (!completed.output.length) completed.output = [...outputItems.entries()].sort(([a], [b]) => a - b).map(([, item]) => item);
    return completed;
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      bytes += value?.byteLength || 0;
      if (bytes > 12 * 1024 * 1024) throw new OpenAIResponseError('incomplete_stream');
      buffer += decoder.decode(value, { stream: !done });
      let boundary: RegExpMatchArray | null;
      while ((boundary = buffer.match(/\r?\n\r?\n/))) {
        const end = boundary.index!;
        const result = parseEvent(buffer.slice(0, end));
        buffer = buffer.slice(end + boundary[0].length);
        if (result) return result;
      }
      if (done) {
        if (buffer.trim()) { const result = parseEvent(buffer); if (result) return result; }
        throw new OpenAIResponseError('incomplete_stream', undefined, requestId);
      }
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

export function apiRequest(options: { model: string; instructions: string; input: unknown[]; tools?: unknown[]; format: unknown }) {
  return { model: options.model, instructions: options.instructions, input: options.input,
    tools: options.tools?.length ? [{ type: 'namespace', name: 'garra', description: 'Read authorized rare disease evidence and workspace records.', tools: options.tools }] : undefined,
    text: { format: options.format }, include: ['reasoning.encrypted_content'], store: false, stream: true };
}
export async function openAIResponse(body: Record<string, unknown>, options: ResponseStreamOptions = {}) {
  if (!process.env.OPENAI_API_KEY) throw new OpenAIResponseError('not_configured');
  const response = await fetch('https://api.openai.com/v1/responses', { method: 'POST', cache: 'no-store', redirect: 'error',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${process.env.OPENAI_API_KEY}` }, body: JSON.stringify(body),
    signal: AbortSignal.any([AbortSignal.timeout(150000), ...(options.signal ? [options.signal] : [])]) });
  return readCompletedResponse(response, options.onTextDelta);
}
