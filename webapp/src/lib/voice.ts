// Server-side transcription. Audio is held in memory and never saved to a record.
export const MAX_AUDIO_BYTES = 8 * 1024 * 1024;
export class VoiceError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function readAudioUpload(req: Request): Promise<File> {
  const type = req.headers.get('content-type') || '';
  if (!type.startsWith('multipart/form-data')) throw new VoiceError(400, 'Send an audio recording.');
  const maximum = MAX_AUDIO_BYTES + 64 * 1024;
  if (Number(req.headers.get('content-length') || 0) > maximum) throw new VoiceError(413, 'Please record a shorter question.');
  const reader = req.body?.getReader();
  if (!reader) throw new VoiceError(400, 'The recording is empty.');
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maximum) { await reader.cancel(); throw new VoiceError(413, 'Please record a shorter question.'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  let form: FormData;
  try { form = await new Response(Buffer.concat(chunks), { headers: { 'Content-Type': type } }).formData(); }
  catch { throw new VoiceError(400, 'The audio upload could not be read. Please record again.'); }
  const file = form.get('audio');
  if (!(file instanceof File) || !file.size) throw new VoiceError(400, 'The recording is empty.');
  if (file.size > MAX_AUDIO_BYTES) throw new VoiceError(413, 'Please record a shorter question.');
  const mime = file.type.split(';')[0].toLowerCase();
  const bytes = Buffer.from(await file.slice(0, 16).arrayBuffer());
  const valid = mime === 'audio/webm' ? bytes.subarray(0, 4).equals(Buffer.from([0x1a, 0x45, 0xdf, 0xa3]))
    : ['audio/mp4', 'audio/x-m4a'].includes(mime) ? bytes.subarray(4, 8).toString() === 'ftyp'
    : mime === 'audio/ogg' ? bytes.subarray(0, 4).toString() === 'OggS'
    : ['audio/wav', 'audio/x-wav'].includes(mime) ? bytes.subarray(0, 4).toString() === 'RIFF' && bytes.subarray(8, 12).toString() === 'WAVE'
    : mime === 'audio/mpeg' ? bytes.subarray(0, 3).toString() === 'ID3' || (bytes[0] === 255 && (bytes[1] & 224) === 224)
    : false;
  if (!valid) throw new VoiceError(415, 'This recording format is not supported. Please try Safari or Chrome.');
  return file;
}

export async function transcribeAudio(file: File, signal?: AbortSignal, fetcher: typeof fetch = fetch) {
  const key = process.env.ELEVENLABS_API_KEY;
  if (!key) throw new VoiceError(503, 'Voice input is not connected yet. You can still type your question.');
  const form = new FormData();
  form.set('file', file, `question.${file.type.includes('mp4') ? 'm4a' : file.type.includes('wav') ? 'wav' : file.type.includes('ogg') ? 'ogg' : file.type.includes('mpeg') ? 'mp3' : 'webm'}`);
  form.set('model_id', process.env.ELEVENLABS_STT_MODEL || 'scribe_v2');
  form.set('tag_audio_events', 'false');
  form.set('diarize', 'false');
  form.set('webhook', 'false');
  const abort = AbortSignal.any([AbortSignal.timeout(60000), ...(signal ? [signal] : [])]);
  let response: Response;
  try {
    response = await fetcher('https://api.elevenlabs.io/v1/speech-to-text', {
      method: 'POST', headers: { 'xi-api-key': key }, body: form, signal: abort,
    });
  } catch {
    throw new VoiceError(504, 'Transcription could not finish. Please try again or type your question.');
  }
  if (!response.ok) {
    // Never forward provider bodies: they can contain request details or audio metadata.
    if (response.status === 429) throw new VoiceError(429, 'Voice transcription is busy. Please try again shortly.');
    if ([401, 403].includes(response.status)) throw new VoiceError(503, 'The voice service key needs speech-to-text access. You can still type your question.');
    throw new VoiceError(502, 'The voice service could not transcribe this recording. Please try again.');
  }
  const result = await response.json().catch(() => null);
  const text = typeof result?.text === 'string' ? result.text.trim() : '';
  if (!text) throw new VoiceError(422, 'No speech was detected. Try speaking a little closer to your microphone.');
  if (text.length > 16000) throw new VoiceError(413, 'The transcript is too long. Please record a shorter question.');
  return { text };
}
