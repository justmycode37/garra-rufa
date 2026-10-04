import assert from 'node:assert/strict';
import test from 'node:test';
import { createAnimationLoop } from '../src/lib/animation-loop';

function frameScheduler() {
  let id = 0, cancellations = 0;
  const queued = new Map<number, (now: number) => void>();
  return {
    request(callback: (now: number) => void) { queued.set(++id, callback); return id; },
    cancel(frame: number) { cancellations++; queued.delete(frame); },
    step(now: number) {
      const callbacks = [...queued.values()]; queued.clear();
      callbacks.forEach(callback => callback(now));
    },
    get pending() { return queued.size; },
    get cancellations() { return cancellations; },
  };
}

test('continuous scroll events neither reset elapsed time nor replace queued animation frames', () => {
  function run(scrolling: boolean) {
    const scheduler = frameScheduler(), elapsed: number[] = [];
    const loop = createAnimationLoop(delta => { elapsed.push(delta); return true; }, scheduler);
    loop.request();
    for (let now = 0; now <= 1024; now += 16) {
      // Trackpads can produce multiple scroll/layout notifications per frame.
      if (scrolling) for (let event = 0; event < 8; event++) loop.request();
      assert.equal(scheduler.pending, 1);
      scheduler.step(now);
    }
    assert.equal(scheduler.cancellations, 0);
    loop.dispose();
    assert.equal(scheduler.pending, 0);
    return elapsed;
  }
  const scrolling = run(true);
  assert.deepEqual(scrolling, run(false));
  assert.equal(scrolling.reduce((sum, delta) => sum + delta, 0), 1024);
});

test('a delayed frame retains all elapsed animation time', () => {
  const scheduler = frameScheduler(), elapsed: number[] = [];
  const loop = createAnimationLoop(delta => { elapsed.push(delta); return true; }, scheduler);
  loop.request(); scheduler.step(100);
  loop.request(); scheduler.step(600);
  assert.deepEqual(elapsed, [0, 500]);
  loop.dispose();
});

test('hiding the page pauses the clock without counting time in the background', () => {
  const scheduler = frameScheduler(), elapsed: number[] = [];
  const loop = createAnimationLoop(delta => { elapsed.push(delta); return true; }, scheduler);
  loop.request(); scheduler.step(0); scheduler.step(32);
  loop.pause(); loop.request();
  assert.equal(scheduler.pending, 0);
  loop.resume(); scheduler.step(10000); scheduler.step(10032);
  assert.deepEqual(elapsed, [0, 32, 0, 32]);
  loop.dispose(); loop.request(); loop.resume();
  assert.equal(scheduler.pending, 0);
});

test('a static reduced-motion scene redraws on demand without a continuous loop', () => {
  const scheduler = frameScheduler(), elapsed: number[] = [];
  const loop = createAnimationLoop(delta => { elapsed.push(delta); return false; }, scheduler);
  loop.request(); scheduler.step(0);
  assert.equal(scheduler.pending, 0);
  loop.request(); loop.request(); scheduler.step(5000);
  assert.deepEqual(elapsed, [0, 0]);
  assert.equal(scheduler.pending, 0);
  loop.dispose();
});
