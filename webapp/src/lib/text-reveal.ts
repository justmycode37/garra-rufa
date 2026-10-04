export type RevealClock = { now: () => number; request: (callback: (time: number) => void) => number; cancel: (id: number) => void };

// Pace network bursts into readable word groups. Time-based progress stays the
// same on fast and slow displays, and catches up gently if the network batches.
export function createTextReveal(onText: (text: string) => void, clock: RevealClock, immediate = false) {
  let target = '', visible = '', budget = 0, lastTime = clock.now();
  let frame: number | undefined, ending = false, stopped = false;
  let resolve: (() => void) | undefined;
  const settle = () => { resolve?.(); resolve = undefined; };
  const tick = (time: number) => {
    frame = undefined;
    if (stopped) return;
    const remaining = target.length - visible.length;
    budget = Math.min(48, budget + Math.min(64, Math.max(0, time - lastTime)) * Math.max(85, remaining / 2.2) / 1000);
    lastTime = time;
    let end = visible.length;
    while (end < target.length) {
      const rest = target.slice(end);
      const word = rest.match(/^\s+|^\S+\s+/)?.[0] || (ending ? rest : '');
      if (!word || (word.length > budget && !(word.length > 48 && budget >= 48))) break;
      end += word.length;
      budget = Math.max(0, budget - word.length);
    }
    if (end !== visible.length) { visible = target.slice(0, end); onText(visible); }
    if (ending && visible === target) settle();
    else if (visible !== target) frame = clock.request(tick);
  };
  const schedule = () => { if (frame === undefined) { lastTime = clock.now(); frame = clock.request(tick); } };
  const update = (text: string) => {
    if (stopped) return;
    // A tool round or replacement answer invalidates the previous draft.
    if (!text.startsWith(target)) { visible = ''; budget = 0; onText(''); }
    target = text;
    if (immediate) { visible = target; onText(visible); if (ending) settle(); }
    else schedule();
  };
  return {
    update,
    finish(text: string) {
      ending = true;
      // Citation validation can adjust the final text. Finish the current
      // reveal and let the caller apply that authoritative result in place,
      // rather than clearing and replaying an already visible answer.
      if (text.startsWith(target)) update(text);
      else schedule();
      return visible === target || stopped ? Promise.resolve() : new Promise<void>(done => { resolve = done; });
    },
    cancel() { stopped = true; if (frame !== undefined) clock.cancel(frame); settle(); },
  };
}
