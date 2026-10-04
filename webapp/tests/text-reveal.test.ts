import test from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Text } from '../src/components/Chat';
import { createTextReveal } from '../src/lib/text-reveal';

function fakeClock() {
  let now = 0, id = 0;
  const frames = new Map<number, (time: number) => void>();
  return {
    now: () => now,
    request: (callback: (time: number) => void) => { frames.set(++id, callback); return id; },
    cancel: (frame: number) => { frames.delete(frame); },
    advance(duration: number) { for (let elapsed = 0; elapsed < duration; elapsed += 16) { now += 16; const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback(now)); } },
    get pending() { return frames.size; },
  };
}

test('network bursts reveal gradually in word groups and finish without dropping the tail', async () => {
  const clock = fakeClock(), updates: string[] = [];
  const answer = 'A comfortable answer with several words, a new paragraph.\n\nAnd an emoji 🐟.';
  const reveal = createTextReveal(text => updates.push(text), clock);
  reveal.update(answer);
  assert.equal(updates.length, 0);
  clock.advance(240);
  assert.ok(updates.length > 0);
  assert.ok(updates.at(-1)!.length < answer.length);
  assert.ok(updates.every(text => answer.startsWith(text) && /\s$/.test(text)));
  const finished = reveal.finish(answer);
  clock.advance(3000);
  await finished;
  assert.equal(updates.at(-1), answer);
  assert.equal(clock.pending, 0);
  assert.ok(updates.every((text, index) => !index || text.length > updates[index - 1].length));
});

test('incomplete words and Markdown prefixes stay buffered across incoming chunks', async () => {
  const clock = fakeClock(), updates: string[] = [];
  const reveal = createTextReveal(text => updates.push(text), clock);
  reveal.update('Connec'); clock.advance(800);
  assert.equal(updates.length, 0);
  reveal.update('Connected communities are available.'); clock.advance(16);
  assert.ok(updates.at(-1)?.startsWith('Connected '));
  const finished = reveal.finish('Connected communities are available.'); clock.advance(2000); await finished;
  assert.equal(updates.at(-1), 'Connected communities are available.');
});

test('resetting a tool draft clears it, cancellation stops updates, and reduced motion is immediate', async () => {
  const clock = fakeClock(), updates: string[] = [];
  const reveal = createTextReveal(text => updates.push(text), clock);
  reveal.update('Draft text with words'); clock.advance(300);
  reveal.update(''); assert.equal(updates.at(-1), '');
  const finish = reveal.finish('A replacement answer.');
  reveal.cancel(); clock.advance(3000); await finish;
  assert.equal(updates.at(-1), '');
  assert.equal(clock.pending, 0);
  const immediate = createTextReveal(text => updates.push(text), clock, true);
  await immediate.finish('No motion needed.');
  assert.equal(updates.at(-1), 'No motion needed.');
  assert.equal(clock.pending, 0);
});

test('streamed Markdown uses word fades while preserving semantic formatting and code', () => {
  const html = renderToStaticMarkup(createElement(Text, { text: '## A heading\n\nSome **bold words** and `code`.', streaming: true }));
  assert.match(html, /<h2><span class="answer-word">A <\/span>/);
  assert.match(html, /<strong><span class="answer-word">bold <\/span>/);
  assert.match(html, /<code>code<\/code>/);
  assert.doesNotMatch(renderToStaticMarkup(createElement(Text, { text: 'Saved answer.' })), /answer-word/);
});

test('final citation corrections do not blank or replay the visible draft', async () => {
  const clock = fakeClock(), updates: string[] = [];
  const reveal = createTextReveal(text => updates.push(text), clock);
  reveal.update('Evidence [4]. More information.'); clock.advance(160);
  const finish = reveal.finish('Evidence [1]. More information.'); clock.advance(2000); await finish;
  assert.ok(updates.every((text, index) => text && (!index || text.startsWith(updates[index - 1]))));
  assert.equal(updates.at(-1), 'Evidence [4]. More information.');
});
