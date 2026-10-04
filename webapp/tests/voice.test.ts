import test from 'node:test';
import assert from 'node:assert/strict';
import { readAudioUpload, transcribeAudio, MAX_AUDIO_BYTES, VoiceError } from '../src/lib/voice';

function audio(type = 'audio/wav', bytes = Buffer.from('RIFF0000WAVEfmt 00000000000000000000')) {
  return new File([bytes], 'test.wav', { type });
}
function upload(file: File) {
  const form = new FormData(); form.set('audio', file);
  return new Request('http://localhost/api/voice', { method: 'POST', body: form });
}
test('audio uploads support Safari MP4 and browser WebM while rejecting disguised files', async () => {
  assert.equal((await readAudioUpload(upload(audio()))).type, 'audio/wav');
  await readAudioUpload(upload(audio('audio/mp4', Buffer.from('0000ftypM4A 0000000'))));
  await readAudioUpload(upload(audio('audio/webm', Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 0, 0]))));
  await assert.rejects(readAudioUpload(upload(audio('audio/wav', Buffer.from('not actual audio')))), (e: unknown) => e instanceof VoiceError && e.status === 415);
  await assert.rejects(readAudioUpload(upload(audio('text/plain'))), (e: unknown) => e instanceof VoiceError && e.status === 415);
  await assert.rejects(readAudioUpload(upload(audio('audio/wav', Buffer.alloc(0)))), (e: unknown) => e instanceof VoiceError && e.status === 400);
});
test('oversized multipart streams are rejected even without Content-Length', async () => {
  const request = new Request('http://localhost/api/voice', {
    method: 'POST', headers: { 'Content-Type': 'multipart/form-data; boundary=test' },
    body: new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(MAX_AUDIO_BYTES + 70 * 1024)); controller.close(); } }),
    ...{ duplex: 'half' },
  });
  assert.equal(request.headers.has('content-length'), false);
  await assert.rejects(readAudioUpload(request), (e: unknown) => e instanceof VoiceError && e.status === 413);
});
test('transcription authenticates server-side and returns only editable text', async () => {
  const previous = process.env.ELEVENLABS_API_KEY;
  process.env.ELEVENLABS_API_KEY = 'test-server-only-key';
  try {
    const mock: typeof fetch = async (url, options) => {
      assert.equal(url, 'https://api.elevenlabs.io/v1/speech-to-text');
      assert.equal(new Headers(options?.headers).get('xi-api-key'), 'test-server-only-key');
      const form = options?.body as FormData;
      assert.equal(form.get('model_id'), process.env.ELEVENLABS_STT_MODEL || 'scribe_v2');
      assert.equal(form.get('webhook'), 'false');
      assert.equal(form.get('diarize'), 'false');
      assert.ok(form.get('file') instanceof File);
      return Response.json({ text: '  Find research about rare diseases.  ', words: [], language_code: 'en', private_metadata: 'hidden' });
    };
    assert.deepEqual(await transcribeAudio(audio(), undefined, mock), { text: 'Find research about rare diseases.' });
    await assert.rejects(transcribeAudio(audio(), undefined, async () => Response.json({ text: '' })), (e: unknown) => e instanceof VoiceError && e.status === 422);
    await assert.rejects(transcribeAudio(audio(), undefined, async () => Response.json({ text: 'x'.repeat(16001) })), (e: unknown) => e instanceof VoiceError && e.status === 413);
  } finally { if (previous === undefined) delete process.env.ELEVENLABS_API_KEY; else process.env.ELEVENLABS_API_KEY = previous; }
});
test('provider authentication failures do not leak response details', async () => {
  const previous = process.env.ELEVENLABS_API_KEY;
  process.env.ELEVENLABS_API_KEY = 'test-key';
  try {
    await assert.rejects(transcribeAudio(audio(), undefined, async () => Response.json({ detail: 'secret provider metadata' }, { status: 401 })), (e: unknown) => e instanceof VoiceError && e.status === 503 && !e.message.includes('secret'));
    await assert.rejects(transcribeAudio(audio(), undefined, async () => { throw new Error('secret request metadata'); }), (e: unknown) => e instanceof VoiceError && e.status === 504 && !e.message.includes('secret'));
    delete process.env.ELEVENLABS_API_KEY;
    await assert.rejects(transcribeAudio(audio()), (e: unknown) => e instanceof VoiceError && e.status === 503);
  } finally { if (previous === undefined) delete process.env.ELEVENLABS_API_KEY; else process.env.ELEVENLABS_API_KEY = previous; }
});
