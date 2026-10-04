import test from 'node:test';
import assert from 'node:assert/strict';
import { AtlasLabelMotion } from '../src/lib/atlas-label-motion';
import type { PlacedLabel } from '../src/lib/atlas-labels';

const label: PlacedLabel = { id: 'heart', title: 'Heart', kind: 'region', target: 'heart', anchor: { x: 460, y: 300 }, x: 38, y: 240, width: 236, height: 38, side: 'left', overlapsGraph: false };
const settle = (motion: AtlasLabelMotion, labels = [label], start = 0) => {
  let frame = motion.update(labels, start);
  for (let time = start + 16; time <= start + 1200; time += 16) frame = motion.update(labels, time);
  assert.equal(frame.animating, false, 'stop requesting frames when transitions finish');
  return frame;
};

test('a label fades in, holds through a brief layout gap, and fades out before removal', () => {
  const motion = new AtlasLabelMotion();
  assert.equal(motion.update([label], 0).labels[0].opacity, 0);
  assert.equal(motion.update([label], 64).labels[0].opacity, 0);
  const entering = motion.update([label], 96);
  assert.ok(entering.labels[0].opacity > 0 && entering.labels[0].opacity < 1);
  assert.equal(entering.labels[0].interactive, false);
  assert.equal(settle(motion, [label], 112).labels[0].opacity, 1);

  // The hold starts at disappearance, even after a long idle period.
  assert.equal(motion.update([], 10000).labels[0].opacity, 1);
  assert.equal(motion.update([], 10064).labels[0].opacity, 1);
  assert.equal(motion.update([label], 10096).labels[0].opacity, 1);
  motion.update([], 10112);
  motion.update([], 10240);
  const exiting = motion.update([], 10272);
  assert.ok(exiting.labels[0].opacity > 0 && exiting.labels[0].opacity < 1);
  assert.equal(exiting.labels[0].interactive, false);
  assert.deepEqual(settle(motion, [], 10288).labels, []);
});

test('reappearing during an exit reverses the fade without remounting at full opacity', () => {
  const motion = new AtlasLabelMotion();
  settle(motion);
  motion.update([], 1216);
  motion.update([], 1344);
  const exiting = motion.update([], 1408).labels[0];
  const returning = motion.update([label], 1424).labels[0];
  assert.ok(returning.opacity > exiting.opacity && returning.opacity < 1);
  assert.equal(returning.id, exiting.id);
  assert.equal(settle(motion, [label], 1440).labels[0].opacity, 1);
});

test('nearby placements move gradually and finish at their destination', () => {
  const motion = new AtlasLabelMotion();
  settle(motion);
  const moved = { ...label, y: label.y + 64, anchor: { x: 470, y: 340 } };
  const frame = motion.update([moved], 1216);
  assert.ok(frame.labels[0].y > label.y && frame.labels[0].y < moved.y);
  assert.deepEqual(frame.labels[0].anchor, moved.anchor);
  assert.equal(frame.labels[0].opacity, 1);
  assert.equal(settle(motion, [moved], 1232).labels[0].y, moved.y);
});

test('side changes fade before relocating and then fade back in', () => {
  const motion = new AtlasLabelMotion();
  settle(motion);
  const moved: PlacedLabel = { ...label, side: 'right', x: 626 };
  const start = motion.update([moved], 1216).labels[0];
  assert.equal(start.x, label.x);
  assert.equal(start.side, 'left');
  assert.ok(start.opacity < 1 && start.opacity > 0);
  assert.equal(start.interactive, false);
  let relocated = false;
  for (let time = 1232; time <= 1808; time += 16) {
    const next = motion.update([moved], time).labels[0];
    if (!relocated && next.side === 'right') {
      assert.equal(next.opacity, 0, 'change alignment only while invisible');
      relocated = true;
    }
    assert.ok(next.x === label.x || next.x === moved.x, 'never sweep across the anatomy');
  }
  assert.ok(relocated);
  assert.equal(settle(motion, [moved], 1824).labels[0].opacity, 1);
});

test('fleeting candidates never flash and reduced motion skips spatial interpolation', () => {
  const motion = new AtlasLabelMotion();
  motion.update([label], 0);
  assert.deepEqual(motion.update([], 16).labels, []);
  settle(motion, [label], 32);
  const moved = { ...label, y: 300 };
  const frame = motion.update([moved], 1248, true);
  assert.equal(frame.labels[0].y, moved.y);
  assert.equal(frame.animating, false);
});

test('new labels still start transparent when the view resumes after being idle', () => {
  const motion = new AtlasLabelMotion();
  settle(motion);
  const frame = motion.update([label, { ...label, id: 'brain', title: 'Brain' }], 100000);
  assert.equal(frame.labels.find(item => item.id === 'brain')!.opacity, 0);
  assert.equal(frame.labels.find(item => item.id === 'heart')!.opacity, 1);
});
