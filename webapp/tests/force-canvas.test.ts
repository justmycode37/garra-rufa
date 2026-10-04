import test from 'node:test';
import assert from 'node:assert/strict';
import { blend, tint } from '../src/lib/force-canvas';

test('blend merges connected colours by weight', () => {
  assert.equal(blend([{ color: '#d59683', weight: 1 }]), '#d59683');
  const mixed = blend([{ color: '#ff0000', weight: 1 }, { color: '#0000ff', weight: 1 }]);
  assert.match(mixed, /^#[0-9a-f]{6}$/);
  const [r, g, b] = [1, 3, 5].map(i => parseInt(mixed.slice(i, i + 2), 16));
  assert.equal(r, b);
  assert.equal(g, 0);
  // more weight pulls the mix towards that colour
  const heavy = blend([{ color: '#ff0000', weight: 3 }, { color: '#0000ff', weight: 1 }]);
  assert.ok(parseInt(heavy.slice(1, 3), 16) > parseInt(heavy.slice(5, 7), 16));
});

test('blend ignores invalid input and tint lightens', () => {
  assert.equal(blend([{ color: 'red', weight: 1 }, { color: '#00ff00', weight: 0 }]), 'red');
  assert.equal(blend([]), '#999999');
  assert.equal(tint('#000000', 0), '#000000');
  assert.equal(tint('#000000', 1), '#ffffff');
});
