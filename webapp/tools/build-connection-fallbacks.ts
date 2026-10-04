/** Run with node --import tsx tools/build-connection-fallbacks.ts. */
import { readFileSync, writeFileSync } from 'node:fs';
import * as THREE from 'three';
import { createReachingHandMotion, type ReachingHandGraph } from '../src/lib/reaching-hand-motion';
import type { ConnectionGraph } from '../src/lib/connection-graph';
import type { HumanRig } from '../src/lib/body-motion';
import { createHandPairLayout, raisedHandMatrix, HAND_SCALE } from '../src/lib/hand-layout';

const root = new URL('../public/models/', import.meta.url);
const body: ConnectionGraph & { rig: HumanRig } = JSON.parse(readFileSync(new URL('human-exploration.json', root), 'utf8'));
const hands: ReachingHandGraph = JSON.parse(readFileSync(new URL('reaching-hands.json', root), 'utf8'));
const format = (n: number) => n.toFixed(3);
function lines(asset: ConnectionGraph, positions: THREE.Vector3[], visible: boolean[]) {
  return Array.from({ length: asset.edges.length / 2 }, (_, i) => {
    const a = asset.edges[i * 2], b = asset.edges[i * 2 + 1];
    if (!visible[a] || !visible[b]) return '';
    return `M${format(positions[a].x)},${format(-positions[a].y)}L${format(positions[b].x)},${format(-positions[b].y)}`;
  }).join('');
}
const svg = (viewBox: string, path: string, width: number) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" fill="none"><path d="${path}" stroke="#141314" stroke-width="${width}" stroke-linecap="round"/></svg>`;
writeFileSync(new URL('body-fallback.svg', root), svg('-4.6 -3.8 7.6 7.6', lines(body,
  body.spacing.map((_, i) => new THREE.Vector3().fromArray(body.nodes, i * 3)),
  body.spacing.map((_, i) => body.normals[i * 3 + 2] > .1)), .008));
const right = createReachingHandMotion(hands.rig, 1), left = createReachingHandMotion(hands.rig, 0);
right.update(0, true); left.update(0, true);
const carrier = raisedHandMatrix(body.rig), pair = createHandPairLayout(hands.rig);
pair.update(carrier, right, left);
const center = new THREE.Matrix4().makeScale(1 / HAND_SCALE, 1 / HAND_SCALE, 1 / HAND_SCALE)
  .multiply(new THREE.Matrix4().makeTranslation(-pair.center.x, -pair.center.y, -pair.center.z));
const handPaths = ([[left, pair.partnerMatrix], [right, carrier]] as const).map(([motion, transform]) => {
  const matrix = center.clone().multiply(transform);
  const positions = hands.spacing.map((_, i) => motion.deform(new THREE.Vector3().fromArray(hands.nodes, i * 3), hands.rig.nodeWeights, i * hands.rig.boneCount).applyMatrix4(matrix));
  const visible = positions.map((p, i) => {
    const normalPoint = new THREE.Vector3().fromArray(hands.nodes, i * 3).addScaledVector(new THREE.Vector3().fromArray(hands.normals, i * 3), .01);
    return motion.deform(normalPoint, hands.rig.nodeWeights, i * hands.rig.boneCount).applyMatrix4(matrix).sub(p).z > 0;
  });
  return lines(hands, positions, visible);
}).join('');
writeFileSync(new URL('hands-fallback.svg', root), svg('-4.35 -1.35 8.7 2.7', handPaths, .005));
