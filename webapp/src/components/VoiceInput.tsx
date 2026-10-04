'use client';

import { useEffect, useRef, useState } from 'react';
import { Mic, Square, LoaderCircle } from 'lucide-react';
import { browserSpeechRecognition, speechError, startBrowserDictation } from '@/lib/browser-voice';

type Phase = 'idle' | 'requesting' | 'recording' | 'stopping' | 'transcribing';
const MAX_SECONDS = 120;

type Props = {
  disabled: boolean;
  onStart: () => void;
  onTranscript: (text: string) => void;
  onFinish: () => void;
  onCancel: () => void;
  onNotice: (text: string) => void;
  onBusyChange: (busy: boolean) => void;
};

export default function VoiceInput(props: Props) {
  const [phase, setPhase] = useState<Phase>('idle');
  const phaseRef = useRef<Phase>('idle');
  const callbacks = useRef(props); callbacks.current = props;
  const serverAvailable = useRef(false);
  const dictation = useRef<ReturnType<typeof startBrowserDictation> | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);

  function transition(next: Phase) { phaseRef.current = next; setPhase(next); }
  function release() {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    stream.current?.getTracks().forEach(track => { track.onended = null; track.stop(); });
    stream.current = null;
  }
  function discard() {
    generation.current++;
    controller.current?.abort(); controller.current = null;
    dictation.current?.cancel(); dictation.current = null;
    const current = recorder.current;
    if (current) {
      current.onstop = null; current.ondataavailable = null; current.onerror = null;
      if (current.state !== 'inactive') current.stop();
    }
    recorder.current = null; chunks.current = []; release();
  }
  function finish(notice = '') {
    discard(); transition('idle');
    callbacks.current.onBusyChange(false);
    callbacks.current.onNotice(notice);
    callbacks.current.onFinish();
  }
  function cancel() {
    discard(); transition('idle');
    callbacks.current.onBusyChange(false);
    callbacks.current.onCancel();
    callbacks.current.onNotice('');
  }
  useEffect(() => {
    const request = new AbortController();
    // Check the optional fallback in advance, so clicking the microphone never
    // waits for a network request before starting browser speech recognition.
    if (!browserSpeechRecognition()) {
      fetch('/api/voice', { signal: AbortSignal.any([request.signal, AbortSignal.timeout(8000)]) })
        .then(async response => { if (response.ok) serverAvailable.current = !!(await response.json()).available; })
        .catch(() => { /* The click reports unavailable dictation when needed. */ });
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape' && phaseRef.current !== 'idle') { event.preventDefault(); cancel(); }
    }
    document.addEventListener('keydown', escape);
    return () => {
      request.abort(); discard(); document.removeEventListener('keydown', escape);
      callbacks.current.onBusyChange(false);
    };
  }, []);

  function stop() {
    if (!['requesting', 'recording'].includes(phaseRef.current)) return;
    transition('stopping'); callbacks.current.onNotice('Finishing…');
    if (dictation.current) { dictation.current.stop(); release(); return; }
    if (recorder.current?.state === 'recording') recorder.current.stop();
    release();
  }
  async function transcribe(audio: Blob, id: number) {
    if (!audio.size) { finish('No audio was captured. Please try again.'); return; }
    transition('transcribing'); callbacks.current.onNotice('Transcribing…');
    const request = new AbortController(); controller.current = request;
    try {
      const form = new FormData(); form.set('audio', audio, 'question');
      const response = await fetch('/api/voice', { method: 'POST', body: form, signal: AbortSignal.any([request.signal, AbortSignal.timeout(65000)]) });
      const result = await response.json();
      if (id !== generation.current) return;
      if (!response.ok || typeof result.text !== 'string') throw new Error(result.error || 'Transcription failed. Please try again.');
      callbacks.current.onTranscript(result.text); finish();
    } catch (error) {
      if (id === generation.current) finish(error instanceof Error ? error.message : 'Transcription failed. Please try again.');
    }
  }
  async function start() {
    if (props.disabled) return;
    discard();
    const Recognition = browserSpeechRecognition();
    const canRecord = serverAvailable.current && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== 'undefined';
    if (!Recognition && !canRecord) {
      callbacks.current.onNotice('Dictation is unavailable in this browser. Open the app in Chrome or Safari to use your microphone.');
      return;
    }
    callbacks.current.onStart(); callbacks.current.onBusyChange(true);
    callbacks.current.onNotice('Opening microphone…'); transition('requesting');
    const id = generation.current;
    function listening() {
      transition('recording'); callbacks.current.onNotice('Listening… Tap the microphone to stop.');
      timer.current = setTimeout(stop, MAX_SECONDS * 1000);
    }
    if (Recognition) {
      try {
        // Keep start() in the original click gesture; no await before this call.
        const control = startBrowserDictation(Recognition, navigator.language, {
          onStart: () => { if (id === generation.current) listening(); },
          onText: text => { if (id === generation.current) callbacks.current.onTranscript(text); },
          onEnd: (text, failure) => {
            if (id !== generation.current) return;
            callbacks.current.onTranscript(text);
            finish(failure || (text ? '' : speechError('no-speech')));
          },
        });
        if (id === generation.current) dictation.current = control;
        else control.cancel();
      } catch { finish(speechError('service-not-allowed')); }
      return;
    }
    let media: MediaStream | null = null;
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
      if (id !== generation.current) { media.getTracks().forEach(track => track.stop()); return; }
      stream.current = media;
      const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'].find(type => MediaRecorder.isTypeSupported(type));
      const current = new MediaRecorder(media, { ...(mime ? { mimeType: mime } : {}), audioBitsPerSecond: 96000 });
      recorder.current = current;
      current.ondataavailable = event => {
        if (id !== generation.current) return;
        if (event.data.size) chunks.current.push(event.data);
        if (chunks.current.reduce((sum, part) => sum + part.size, 0) > 7 * 1024 * 1024) stop();
      };
      current.onstop = () => {
        if (id !== generation.current) return;
        const audio = new Blob(chunks.current, { type: current.mimeType || chunks.current[0]?.type || 'audio/webm' });
        chunks.current = []; release();
        void transcribe(audio, id);
      };
      current.onerror = () => { if (id === generation.current) finish('Recording stopped unexpectedly. Please try again.'); };
      media.getAudioTracks().forEach(track => { track.onended = stop; });
      current.start(500); listening();
    } catch (error) {
      media?.getTracks().forEach(track => track.stop());
      if (id === generation.current) finish(error instanceof DOMException && error.name === 'NotAllowedError' ? speechError('not-allowed') : speechError('audio-capture'));
    }
  }

  const active = phase !== 'idle';
  const waiting = phase === 'requesting' || phase === 'stopping' || phase === 'transcribing';
  const label = phase === 'recording' ? 'Stop listening' : active ? 'Cancel voice input' : 'Use voice input';
  return <button type="button" className={`voice-button ${active ? 'active' : ''} ${phase === 'recording' ? 'listening' : ''}`}
    aria-label={label} aria-pressed={active} disabled={props.disabled && !active}
    title={active ? label : 'Dictate into your question using your browser’s speech service'}
    onClick={phase === 'idle' ? start : phase === 'recording' ? stop : cancel}>
    {waiting ? <LoaderCircle size={17} className="spin"/> : phase === 'recording' ? <Square size={12} fill="currentColor"/> : <Mic size={19}/>}
  </button>;
}
