import { body, handler, HttpError, json, requireUser } from '@/lib/http';
import { conversationTransferSchema, ConversationUnavailable, saveLandingConversation } from '@/lib/chat-transfer';
import { limit } from "@/lib/persistence";

export const POST = handler(async request => {
  const user = await requireUser();
  if (!(await limit(`chat-transfer:${user.id}`, 40, 10 * 60000))) throw new HttpError(429, 'Please wait a moment before continuing.');
  const input = (await conversationTransferSchema.parseAsync(await body(request)));
  try { return json({ record: (await saveLandingConversation(user.id, input)) }); }
  catch (error) {
    if (error instanceof ConversationUnavailable) throw new HttpError(404, error.message);
    throw error;
  }
});
