import test from 'node:test';
import assert from 'node:assert/strict';
import { placeAtlasCallouts } from '../src/lib/atlas-callouts';

const records = Array.from({ length: 8 }, (_, i) => ({ id: `record-${i}`, label: `Associated condition ${i}` }));
const anchors = Array.from({ length: 60 }, (_, i) => ({ id: `vertex-${i}`, x: 430 + i % 6 * 28, y: 245 + Math.floor(i / 6) * 20 }));
const viewport = { width: 1100, height: 850 };
const focus = { x: 500, y: 340 };

test('every label connects to a distinct anatomy vertex and labels surround the organ without overlapping', () => {
  const labels = placeAtlasCallouts(records, anchors, viewport, focus);
  assert.equal(labels.length, records.length);
  assert.equal(new Set(labels.map(label => label.anchor.id)).size, labels.length);
  for (const label of labels) {
    assert.deepEqual(label.anchor, anchors.find(point => point.id === label.anchor.id));
    assert.ok(label.side === 'left' ? label.x + label.width < focus.x : label.x > focus.x);
    for (const other of labels) if (label !== other) assert.ok(label.x + label.width <= other.x || other.x + other.width <= label.x || label.y + label.height <= other.y || other.y + other.height <= label.y);
  }
});

test('panning moves the surrounding list and keeps each leader on its original model vertex', () => {
  const initial = placeAtlasCallouts(records, anchors, viewport, focus);
  const shifted = placeAtlasCallouts(records, anchors.map(p => ({ ...p, x: p.x + 20, y: p.y + 10 })), viewport, { x: focus.x + 20, y: focus.y + 10 }, initial);
  for (const [i, label] of shifted.entries()) {
    assert.equal(label.anchor.id, initial[i].anchor.id);
    assert.equal(label.x, initial[i].x + 20);
    assert.equal(label.y, initial[i].y + 10);
  }
});

test('offscreen anatomy does not leave disconnected labels floating on the screen', () => {
  assert.deepEqual(placeAtlasCallouts(records, anchors.map(p => ({ ...p, x: p.x + 2000 })), viewport, focus), []);
  assert.deepEqual(placeAtlasCallouts(records, [], viewport, focus), []);
});

test('mobile labels fit on screen above the composer', () => {
  const points = anchors.map(p => ({ ...p, x: 175 + (p.x - 430) / 5, y: p.y }));
  const labels = placeAtlasCallouts(records.slice(0, 4), points, { width: 390, height: 750 }, { x: 195, y: 340 });
  assert.equal(labels.length, 4);
  for (const label of labels) { assert.ok(label.x >= 18 && label.x + label.width <= 372); assert.ok(label.y >= 138 && label.y + label.height <= 540); }
});
