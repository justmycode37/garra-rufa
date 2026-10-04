import test from 'node:test';
import assert from 'node:assert/strict';
import { placeAtlasLabels, type LabelCandidate } from '../src/lib/atlas-labels';

const candidates: LabelCandidate[] = [
  { id: 'a', title: 'Huntington disease', anchor: { x: 440, y: 250 }, kind: 'disease' },
  { id: 'b', title: 'Rett syndrome', anchor: { x: 470, y: 275 }, kind: 'disease' },
];

test('labels stay outside the body and do not overlap each other', () => {
  const outline = Array.from({ length: 71 }, () => ({ left: 330, right: 570 }));
  const labels = placeAtlasLabels(candidates, outline, 900, 700);
  assert.equal(labels.length, 2);
  for (const label of labels) {
    assert.ok(label.x + label.width < 330 || label.x > 570);
    assert.equal(label.overlapsGraph, false);
  }
  const [a, b] = labels;
  assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y);
  assert.notDeepEqual(a.anchor, b.anchor);
});

test('disease labels remain visible when the body fills the frame', () => {
  const outline = Array.from({ length: 71 }, () => ({ left: -200, right: 1200 }));
  const labels = placeAtlasLabels(candidates, outline, 900, 700);
  assert.equal(labels.length, candidates.length);
  for (const label of labels) {
    assert.ok(label.x >= 0 && label.x + label.width <= 900);
    assert.ok(label.y >= 0 && label.y + label.height <= 700);
    assert.deepEqual(label.anchor, candidates.find(candidate => candidate.id === label.id)!.anchor);
    assert.equal(label.overlapsGraph, true);
  }
  const [a, b] = labels;
  assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y);
});

test('labels find free space when only one side is available', () => {
  const outline = Array.from({ length: 71 }, () => ({ left: 350, right: 950 }));
  const labels = placeAtlasLabels(candidates, outline, 900, 700);
  assert.equal(labels.length, 2);
  assert.ok(labels.every(label => label.side === 'left'));
});

test('a disease stays visible as its anatomical point moves offscreen', () => {
  const outline = Array.from({ length: 71 }, () => ({ left: Infinity, right: -Infinity }));
  const labels = placeAtlasLabels([{ ...candidates[0], anchor: { x: -400, y: 250 } }], outline, 900, 700);
  assert.equal(labels.length, 1);
  assert.equal(labels[0].anchor.x, -400);
  assert.ok(labels[0].x >= 0);
  assert.deepEqual(placeAtlasLabels([{ ...candidates[0], kind: 'organ', anchor: { x: -400, y: 250 } }], outline, 900, 700), []);
});

test('five disease titles fit a crowded mobile canvas without hiding or overlapping', () => {
  const outline = Array.from({ length: 61 }, () => ({ left: -100, right: 800 }));
  const crowded: LabelCandidate[] = Array.from({ length: 5 }, (_, i) => ({ id: String(i), title: ['Charcot–Marie–Tooth disease', 'Ehlers–Danlos syndrome', 'Fabry disease', 'Marfan syndrome', 'Rett syndrome'][i], anchor: { x: 190 + i * 4, y: 240 + i * 5 }, kind: 'disease' }));
  const labels = placeAtlasLabels(crowded, outline, 390, 600);
  assert.equal(labels.length, 5);
  for (const a of labels) for (const b of labels) {
    if (a.id === b.id) continue;
    assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y);
  }
});

test('tiny movements across the center keep the same margin and row', () => {
  const outline = Array.from({ length: 71 }, () => ({ left: 330, right: 570 }));
  const candidate = { ...candidates[0], anchor: { x: 449, y: 250 } };
  const initial = placeAtlasLabels([candidate], outline, 900, 700);
  const shifted = placeAtlasLabels([{ ...candidate, anchor: { x: 451, y: 251 } }], outline, 900, 700, initial);
  assert.equal(shifted[0].side, initial[0].side);
  assert.equal(shifted[0].y, initial[0].y);
});

test('candidate ranking changes do not reorder established labels', () => {
  const outline = Array.from({ length: 71 }, () => ({ left: 330, right: 570 }));
  const initial = placeAtlasLabels(candidates, outline, 900, 700);
  const reordered = placeAtlasLabels([...candidates].reverse(), outline, 900, 700, initial);
  assert.deepEqual(reordered, initial);
});

test('placement stability still yields when a margin is no longer clear', () => {
  const outline = Array.from({ length: 71 }, () => ({ left: 330, right: 570 }));
  const initial = placeAtlasLabels([candidates[0]], outline, 900, 700);
  assert.equal(initial[0].side, 'left');
  const blocked = Array.from({ length: 71 }, () => ({ left: 0, right: 570 }));
  const shifted = placeAtlasLabels([candidates[0]], blocked, 900, 700, initial);
  assert.equal(shifted[0].side, 'right');
  assert.equal(shifted[0].overlapsGraph, false);
});
