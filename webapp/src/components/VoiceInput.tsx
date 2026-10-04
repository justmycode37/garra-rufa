'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { Mic, Square, X, LoaderCircle } from 'lucide-react';
import ThinkingIndicator from './ThinkingIndicator';

type Phase = 'idle' | 'checking' | 'requesting' | 'recording' | 'ready' | 'transcribing';
const MAX_SECONDS = 120;

export default function VoiceInput({ disabled, onTranscript, onBusyChange }: {
  disabled: boolean; onTranscript: (text: string) => void; onBusyChange: (busy: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>('idle');
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState('');
  const [available, setAvailable] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const recording = useRef<Blob | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const regionId = useId();

  function release() {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    stream.current?.getTracks().forEach(track => { track.onended = null; track.stop(); });
    stream.current = null;
  }
  function discard() {
    generation.current++;
    controller.current?.abort();
    const current = recorder.current;
    if (current) {
      current.onstop = null; current.ondataavailable = null; current.onerror = null;
      if (current.state !== 'inactive') current.stop();
    }
    recorder.current = null;
    release(); recording.current = null; chunks.current = [];
  }
  useEffect(() => () => { discard(); }, []);
  useEffect(() => {
    onBusyChange(open);
    return () => onBusyChange(false);
  }, [open, onBusyChange]);

  function close() {
    discard(); setOpen(false); setPhase('idle'); setError('');
    trigger.current?.focus();
  }
  async function show() {
    if (disabled) return;
    setOpen(true); setPhase('checking'); setError(''); setSeconds(0); setAvailable(false);
    const id = ++generation.current;
    const request = new AbortController(); controller.current = request;
    try {
      const res = await fetch('/api/voice', { signal: request.signal });
      const config = await res.json();
      if (id !== generation.current) return;
      if (!res.ok || !config.available) throw new Error('Voice input is not connected yet. You can still type your question.');
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('This browser cannot record audio here. Try Safari or Chrome over HTTPS or localhost.');
      setAvailable(true);
    } catch (e) {
      if (id === generation.current) setError(e instanceof Error ? e.message : 'Voice input is unavailable.');
    } finally { if (id === generation.current) setPhase('idle'); }
  }
  function stop() {
    const current = recorder.current;
    if (current?.state === 'recording') current.stop();
    release();
  }
  async function start() {
    discard(); setError(''); setSeconds(0); setPhase('requesting');
    const id = generation.current;
    let media: MediaStream | null = null;
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
      if (id !== generation.current) { media.getTracks().forEach(t => t.stop()); return; }
      stream.current = media;
      const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'].find(type => MediaRecorder.isTypeSupported(type));
      const current = new MediaRecorder(media, { ...(mime ? { mimeType: mime } : {}), audioBitsPerSecond: 96000 });
      recorder.current = current;
      current.ondataavailable = event => {
        if (event.data.size) chunks.current.push(event.data);
        if (chunks.current.reduce((sum, part) => sum + part.size, 0) > 7 * 1024 * 1024) stop();
      };
      current.onstop = () => {
        if (id !== generation.current) return;
        recording.current = new Blob(chunks.current, { type: current.mimeType || chunks.current[0]?.type || 'audio/webm' });
        chunks.current = []; release();
        if (!recording.current.size) { setError('No audio was captured. Please try again.'); setPhase('idle'); }
        else setPhase('ready');
      };
      current.onerror = () => { discard(); setError('Recording stopped unexpectedly. Please try again.'); setPhase('idle'); };
      media.getAudioTracks().forEach(track => { track.onended = stop; });
      current.start(500);
      setPhase('recording');
      const began = performance.now();
      timer.current = setInterval(() => {
        const elapsed = Math.floor((performance.now() - began) / 1000);
        setSeconds(Math.min(elapsed, MAX_SECONDS));
        if (elapsed >= MAX_SECONDS) stop();
      }, 250);
    } catch (e) {
      media?.getTracks().forEach(track => track.stop());
      if (id !== generation.current) return;
      release(); setPhase('idle');
      setError(e instanceof DOMException && e.name === 'NotAllowedError' ? 'Microphone access was denied. Allow it in your browser settings, or type your question.' : 'Your microphone could not be opened. Check that it is connected and try again.');
    }
  }
  async function transcribe() {
    if (!recording.current) return;
    setPhase('transcribing'); setError('');
    const id = generation.current;
    const request = new AbortController(); controller.current = request;
    try {
      const form = new FormData(); form.set('audio', recording.current, 'question');
      const res = await fetch('/api/voice', { method: 'POST', body: form, signal: AbortSignal.any([request.signal, AbortSignal.timeout(65000)]) });
      const result = await res.json();
      if (id !== generation.current) return;
      if (!res.ok) throw new Error(result.error || 'Transcription failed. Please try again.');
      onTranscript(result.text);
      discard(); setOpen(false); setPhase('idle');
    } catch (e) {
      if (id !== generation.current) return;
      setError(e instanceof Error ? e.message : 'Transcription failed. Please try again.');
      setPhase('ready');
    }
  }

  return <>
    <button ref={trigger} type="button" className={`voice-button ${open ? 'active' : ''}`} aria-label="Use voice input" aria-expanded={open} aria-controls={regionId} disabled={disabled && !open} onClick={open ? close : show} title="Use voice input"><Mic size={19}/></button>
    {open && <section id={regionId} className="voice-panel" aria-label="Voice input" onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } }}>
      <div className="voice-panel-heading"><b>{phase === 'recording' ? 'Listening…' : phase === 'ready' ? 'Recording ready' : phase === 'transcribing' ? 'Transcribing…' : 'Ask with your voice'}</b><button type="button" className="icon-button" aria-label="Cancel voice input" onClick={close}><X size={16}/></button></div>
      <p>ElevenLabs transcribes your recording. Review the text before sending.</p>
      {error && <p className="voice-error" role="alert">{error}</p>}
      <div className="voice-panel-actions">
        {phase === 'transcribing' ? <ThinkingIndicator label="Turning speech into text"/> : phase === 'checking' || phase === 'requesting' ? <span role="status"><LoaderCircle size={16} className="spin"/>{phase === 'requesting' ? 'Waiting for microphone…' : 'Connecting…'}</span>
          : phase === 'recording' ? <><span className="recording-clock"><i/>{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')} / 2:00</span><button type="button" className="primary" onClick={stop}><Square size={12} fill="currentColor"/>Stop</button></>
          : phase === 'ready' ? <><button type="button" className="text-button" onClick={start}>Record again</button><button type="button" className="primary" onClick={transcribe}>Transcribe</button></>
          : available && <button type="button" className="primary" onClick={start}><Mic size={15}/>Record</button>}
      </div>
    </section>}
  </>;
}
