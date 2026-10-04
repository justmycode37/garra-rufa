import { handler, json, currentUser, HttpError } from '@/lib/http';
import { limit } from "@/lib/persistence";
import { readAudioUpload, transcribeAudio, VoiceError } from '@/lib/voice';

export const runtime = 'nodejs';
export const maxDuration = 65;
export const GET = handler(async () => json({ available: Boolean(process.env.ELEVENLABS_API_KEY) }));
export const POST = handler(async req => {
  const user = await currentUser();
  // Guest and public callers share a budget, so creating sessions cannot bypass it.
  if (!(await limit(user && !user.guest ? `voice:${user.id}` : 'voice:public', 15, 10 * 60000))) {
    throw new HttpError(429, 'Voice input is busy. Please try again shortly.');
  }
  try {
    if (!process.env.ELEVENLABS_API_KEY) throw new VoiceError(503, 'Voice input is not connected yet. You can still type your question.');
    const file = await readAudioUpload(req);
    return json(await transcribeAudio(file, req.signal));
  } catch (error) {
    if (error instanceof VoiceError) throw new HttpError(error.status, error.message);
    throw error;
  }
});
