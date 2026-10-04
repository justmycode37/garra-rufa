import { z } from 'zod';
import { diseases } from './knowledge';
import { getRecord, saveRecord } from "./persistence";
import type { Disease, Message } from './types';
import { sourceSchema } from './source-schema';
import { listConditions } from "./persistence";

const messageSchema = z.object({
  id: z.string().min(1).max(100),
  role: z.enum(['user', 'assistant']),
  text: z.string().min(1).max(60000),
  sources: z.array(sourceSchema).max(40).optional(),
  diseases: z.array(z.object({ id: z.string().max(80) })).max(8).transform(items => items.map(item => diseases.find(disease => disease.id === item.id)).filter((disease): disease is Disease => !!disease)).optional(),
  communities: z.array(z.object({ id: z.string().min(1).max(80) })).max(3).transform(async items => {
    const conditions = (await listConditions());
    return [...new Set(items.map(item => item.id))].flatMap(id => {
      const condition = conditions.find(item => item.id === id);
      return condition ? [{ id, name: condition.name, memberCount: condition.memberCount, postCount: condition.postCount }] : [];
    });
  }).optional(),
  steps: z.array(z.string().max(4000)).max(3).optional(),
  warning: z.string().max(4000).optional(),
  mode: z.enum(['ai', 'database']).optional(),
  model: z.string().max(100).optional(),
  effort: z.string().max(100).optional(),
  region: z.enum(['body', 'brain', 'heart', 'hands', 'legs', 'muscles']).optional(),
  suggestion: z.object({ kind: z.enum(['project', 'note']), title: z.string().max(160), content: z.string().max(60000) }).nullable().optional(),
});

export const conversationTransferSchema = z.object({
  chatId: z.uuid().optional(),
  messages: z.array(messageSchema).min(2).max(80).refine(messages => messages[0]?.role === 'user' && messages.at(-1)?.role === 'assistant', 'Complete an answer before continuing.'),
});

export class ConversationUnavailable extends Error {}

export async function saveLandingConversation(userId: string, input: { chatId?: string; messages: Message[] }) {
  const previous = input.chatId ? (await getRecord(input.chatId, userId)) : null;
  if (input.chatId && (!previous || previous.kind !== 'chat' || previous.ownerId !== userId)) {
    throw new ConversationUnavailable('This conversation is no longer available. Start a new conversation to continue.');
  }
  const messages: Message[] = input.messages;
  return (await saveRecord(userId, { id: previous?.id, kind: 'chat', title: previous?.title || messages[0].text.slice(0, 65), content: '', messages }));
}
