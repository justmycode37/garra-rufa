import type { Message, SearchResult } from './types';

export type SearchResponse = SearchResult & { messages: Message[]; chatId?: string };
export type AnswerProgress = { type: 'delta'; delta: string } | { type: 'reset' };
export type SearchStreamEvent = AnswerProgress | { type: 'complete'; result: SearchResponse } | { type: 'error'; error: string };
export type OnAnswerText = (text: string) => void;

export function updateStreamingMessage(messages: Message[], id: string, text: string): Message[] {
  if (!text) return messages.filter(message => message.id !== id);
  const message: Message = { id, role: 'assistant', text, mode: 'ai', streaming: true };
  return messages.some(item => item.id === id)
    ? messages.map(item => item.id === id ? message : item)
    : [...messages, message];
}

// A completed event is authoritative: partial answers are never saved or treated
// as successful when the connection ends early.
export async function readSearchResponse(response: Response, onText: OnAnswerText): Promise<SearchResponse> {
  if (!response.ok || !response.headers.get('content-type')?.includes('application/x-ndjson')) {
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to complete your question.');
    return data as SearchResponse;
  }
  if (!response.body) throw new Error('The answer was interrupted. Please try again.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', text = '';
  const parse = (line: string) => {
    if (!line.trim()) return;
    const event = JSON.parse(line) as SearchStreamEvent;
    if (event.type === 'error') throw new Error(event.error);
    if (event.type === 'complete') return event.result;
    if (event.type === 'delta') { text += event.delta; onText(text); }
    if (event.type === 'reset') { text = ''; onText(text); }
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary: number;
      while ((boundary = buffer.indexOf('\n')) !== -1) {
        const result = parse(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 1);
        if (result) return result;
      }
      if (done) {
        const result = parse(buffer);
        if (result) return result;
        throw new Error('The answer was interrupted. Please try again.');
      }
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
