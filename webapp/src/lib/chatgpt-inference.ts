import { z } from 'zod';
import { accountById, saveAccount } from './chatgpt-store';
import { chatGPTAccess, CHATGPT_RESOURCE } from './chatgpt';

export class ChatGPTInferenceError extends Error {
  constructor(public code: string, public status?: number, public requestId?: string, public param?: string) {
    super(inferenceMessage(code, status));
  }
}
function inferenceMessage(code: string, status?: number) {
  if (code === 'subscription_sharing_usage_limit_exceeded') return 'Your ChatGPT plan or Garra Rufa usage limit was reached. Open Manage usage to review it.';
  if (code === 'subscription_sharing_user_not_eligible') return 'ChatGPT plan usage is unavailable for this account or workspace. Check your ChatGPT settings.';
  if (code === 'subscription_sharing_invalid_user' || status === 401) return 'ChatGPT could not verify this connection. Please reconnect from your account menu.';
  if (code === 'subscription_sharing_unsupported_capability') return 'This request includes a capability your ChatGPT plan does not support. Try a text-only question.';
  if (status === 403) return 'ChatGPT plan access was restricted by account, workspace, or integration policy.';
  if (status === 429) return 'ChatGPT is limiting requests. Review Manage usage and try again later.';
  if (code === 'incomplete_stream') return 'The ChatGPT response did not finish. Please try again.';
  return 'ChatGPT is temporarily unavailable. Your connection has been kept; please try again later.';
}

const providerError = z.object({ code: z.string().optional(), param: z.string().nullable().optional() }).passthrough();
function inferenceError(body: unknown, status?: number, requestId?: string) {
  const wrapper = z.object({ error: z.unknown().optional() }).passthrough().safeParse(body);
  const detail = providerError.safeParse(wrapper.success && wrapper.data.error ? wrapper.data.error : body);
  return new ChatGPTInferenceError(detail.success ? detail.data.code || 'request_failed' : 'request_failed', status, requestId, detail.success ? detail.data.param || undefined : undefined);
}

const responseSchema = z.object({ status: z.literal('completed'), output: z.array(z.record(z.string(), z.unknown())) }).passthrough();
const completedItemSchema = z.object({ output_index: z.number().int().nonnegative(), item: z.record(z.string(), z.unknown()) });
export type CompletedResponse = z.infer<typeof responseSchema>;

export async function readCompletedResponse(response: Response): Promise<CompletedResponse> {
  const requestId = response.headers.get('x-request-id') || response.headers.get('openai-request-id') || undefined;
  if (!response.ok) throw inferenceError(await response.json().catch(() => ({})), response.status, requestId);
  if (!response.body) throw new ChatGPTInferenceError('incomplete_stream');
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
    if (event.type === 'response.incomplete') throw new ChatGPTInferenceError('incomplete_stream', undefined, requestId);
    if (event.type === 'response.output_item.done') {
      const { output_index, item } = completedItemSchema.parse(event);
      outputItems.set(output_index, item);
    }
    if (event.type !== 'response.completed') return null;
    const completed = responseSchema.parse(event.response);
    // Some streams send the full items only in output_item.done, leaving the
    // terminal output empty. Preserve messages, tool calls and encrypted reasoning,
    // but never return any of them until the whole response has completed.
    if (!completed.output.length) completed.output = [...outputItems.entries()].sort(([a], [b]) => a - b).map(([, item]) => item);
    return completed;
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      bytes += value?.byteLength || 0;
      if (bytes > 12 * 1024 * 1024) throw new ChatGPTInferenceError('incomplete_stream');
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
        throw new ChatGPTInferenceError('incomplete_stream', undefined, requestId);
      }
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

const catalogSchema = z.object({ models: z.array(z.object({ slug: z.string(), display_name: z.string(), visibility: z.string() })) });
export async function selectChatGPTModel(accessToken: string, preferred?: string) {
  const response = await fetch(`${CHATGPT_RESOURCE}/models`, { headers: { Authorization: `Bearer ${accessToken}` }, signal: AbortSignal.timeout(15000), cache: 'no-store', redirect: 'error' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw inferenceError(data, response.status, response.headers.get('x-request-id') || undefined);
  const models = catalogSchema.parse(data).models.filter(model => model.visibility === 'list');
  const model = models.find(model => model.slug === preferred) || models[0];
  if (!model) throw new Error('No supported models are available for this ChatGPT account. Check your ChatGPT settings.');
  return model.slug;
}

export function subscriptionRequest(options: { model: string; instructions: string; input: unknown[]; tools?: unknown[]; format: unknown }) {
  return { model: options.model, instructions: options.instructions, input: options.input,
    tools: options.tools?.length ? [{ type: 'namespace', name: 'garra', description: 'Read authorized rare disease evidence and workspace records.', tools: options.tools }] : undefined,
    text: { format: options.format }, include: ['reasoning.encrypted_content'], store: false, stream: true };
}

export async function chatGPTResponse(userId: string, body: ReturnType<typeof subscriptionRequest>) {
  const { accessToken, accountId } = await chatGPTAccess(userId);
  try {
    const response = await fetch(`${CHATGPT_RESOURCE}/responses`, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` },
      body: JSON.stringify(body), signal: AbortSignal.timeout(150000), cache: 'no-store', redirect: 'error' });
    return await readCompletedResponse(response);
  } catch (error) {
    // Only confirmed invalid subscriber context invalidates credentials; preserve them on infrastructure errors.
    if (error instanceof ChatGPTInferenceError && error.code === 'subscription_sharing_invalid_user') {
      const account = accountById(accountId);
      if (account?.tokens?.accessToken === accessToken) saveAccount({ ...account, tokens: undefined });
    }
    throw error;
  }
}
