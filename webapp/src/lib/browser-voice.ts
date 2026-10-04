// Browser dictation needs no application API credential. The browser may use its
// own speech service; the UI discloses that before the user starts the microphone.
export interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onstart: (() => void) | null;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
export type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

export function browserSpeechRecognition(): SpeechRecognitionConstructor | undefined {
  if (typeof window === 'undefined') return undefined;
  const browser = window as unknown as { SpeechRecognition?: SpeechRecognitionConstructor; webkitSpeechRecognition?: SpeechRecognitionConstructor };
  return browser.SpeechRecognition || browser.webkitSpeechRecognition;
}

export function speechError(code: string) {
  if (code === 'not-allowed') return 'Microphone access was denied. Allow microphone access for this site in your browser settings, then try again.';
  if (code === 'audio-capture') return 'Your microphone could not be opened. Check that it is connected and available, then try again.';
  if (code === 'no-speech') return 'No speech was detected. Try speaking a little closer to your microphone.';
  if (code === 'network') return 'The browser’s speech service could not connect. Check your connection and try again.';
  if (code === 'language-not-supported') return 'Your browser does not support dictation in its current language. Try another browser language.';
  return 'This browser could not start dictation. Open the app in Chrome or Safari; Safari may also need Siri enabled.';
}

export function startBrowserDictation(Recognition: SpeechRecognitionConstructor, language: string, callbacks: {
  onStart: () => void;
  onText: (text: string) => void;
  onEnd: (text: string, error?: string) => void;
}, timeoutMs = 30000) {
  const recognition = new Recognition();
  recognition.lang = language || 'en-US';
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  let active = true, text = '', startup: ReturnType<typeof setTimeout> | undefined;
  function detach() {
    clearTimeout(startup);
    recognition.onstart = null; recognition.onresult = null;
    recognition.onerror = null; recognition.onend = null;
  }
  function finish(error?: string) {
    if (!active) return;
    active = false; detach();
    callbacks.onEnd(text.trim(), error);
  }
  recognition.onstart = () => { clearTimeout(startup); if (active) callbacks.onStart(); };
  recognition.onresult = event => {
    if (!active) return;
    // Results are cumulative: replace the preview rather than appending repeated
    // interim hypotheses and duplicating every word in the completed transcript.
    text = Array.from(event.results).map(result => result[0]?.transcript || '').join(' ').trim();
    callbacks.onText(text);
  };
  recognition.onerror = event => {
    finish(speechError(event.error));
    try { recognition.abort(); } catch { /* Already stopped. */ }
  };
  recognition.onend = () => finish();
  startup = setTimeout(() => {
    finish(speechError('service-not-allowed'));
    try { recognition.abort(); } catch { /* Already stopped. */ }
  }, timeoutMs);
  try { recognition.start(); }
  catch (error) {
    active = false; detach();
    try { recognition.abort(); } catch { /* No session was started. */ }
    throw error;
  }
  return {
    stop() {
      if (!active) return;
      // Give the engine a chance to return its last words before ending. Engines
      // that omit onend must not leave the composer permanently disabled.
      clearTimeout(startup);
      startup = setTimeout(() => {
        finish();
        try { recognition.abort(); } catch { /* Already stopped. */ }
      }, 3000);
      try { recognition.stop(); } catch { finish(); }
    },
    cancel() {
      active = false; detach();
      try { recognition.abort(); } catch { /* Already stopped. */ }
    },
  };
}
