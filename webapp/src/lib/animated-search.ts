import { readSearchResponse, type OnAnswerText } from './search-stream';
import { createTextReveal } from './text-reveal';

export async function readAnimatedSearchResponse(response: Response, onText: OnAnswerText, signal: AbortSignal) {
  const reveal = createTextReveal(onText, {
    now: () => performance.now(), request: callback => requestAnimationFrame(callback), cancel: id => cancelAnimationFrame(id),
  }, window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const cancel = () => reveal.cancel();
  signal.addEventListener('abort', cancel, { once: true });
  try {
    signal.throwIfAborted();
    const result = await readSearchResponse(response, reveal.update);
    if (result.mode === 'ai') await reveal.finish(result.answer);
    signal.throwIfAborted();
    return result;
  } finally { reveal.cancel(); signal.removeEventListener('abort', cancel); }
}
