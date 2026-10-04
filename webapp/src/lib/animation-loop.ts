type FrameScheduler = {
  request: (callback: (now: number) => void) => number;
  cancel: (id: number) => void;
};

/** Coalesce layout/scroll updates without restarting the animation clock. */
export function createAnimationLoop(
  draw: (elapsedMs: number) => boolean,
  scheduler: FrameScheduler = {
    request: callback => requestAnimationFrame(callback),
    cancel: id => cancelAnimationFrame(id),
  },
) {
  let frame: number | null = null, last: number | null = null;
  let paused = false, disposed = false;

  function request() {
    if (!paused && !disposed && frame === null) frame = scheduler.request(tick);
  }

  function tick(now: number) {
    frame = null;
    if (paused || disposed) return;
    if (last !== null && now - last < 32) { request(); return; }
    const elapsedMs = last === null ? 0 : Math.max(0, now - last);
    last = now;
    if (draw(elapsedMs)) request();
    else last = null;
  }

  function pause() {
    paused = true;
    if (frame !== null) scheduler.cancel(frame);
    frame = null; last = null;
  }

  return {
    request,
    pause,
    resume() { paused = false; request(); },
    dispose() { disposed = true; pause(); },
  };
}
