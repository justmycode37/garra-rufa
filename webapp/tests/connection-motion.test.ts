import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import * as THREE from 'three';
import { createBodyMotion, type HumanRig } from '../src/lib/body-motion';
import { createReachingHandMotion, isReachingHandGraph, type ReachingHandGraph } from '../src/lib/reaching-hand-motion';
import { createHandPairLayout, raisedHandMatrix, HAND_GAP, FIGURE_ORIENTATION, createHandAttachment } from '../src/lib/hand-layout';
import { advanceConnection, connectionTiming, refinementFromZoom } from '../src/lib/connection-timing';
import { createConnectionGraph } from '../src/lib/connection-graph';

const hand: ReachingHandGraph = JSON.parse(readFileSync(new URL('../public/models/reaching-hands.json', import.meta.url), 'utf8'));
const { rig }: { rig: HumanRig } = JSON.parse(readFileSync(new URL('../public/models/human-exploration.json', import.meta.url), 'utf8'));

test('stale or incomplete hand assets are rejected before rendering', () => {
  assert.equal(isReachingHandGraph(hand), true);
  assert.equal(isReachingHandGraph({ ...hand, schemaVersion: 1 }), false);
  assert.equal(isReachingHandGraph({ ...hand, surfaceNormals: undefined }), false);
  assert.equal(isReachingHandGraph({ ...hand, body: { ...hand.body, originalNodeWeights: [] } }), false);
});

test('the reaching pose converges from any point in the body idle cycle', () => {
  const motion = createBodyMotion(rig);
  const wrist = new THREE.Vector3().fromArray(rig.joints, rig.jointNames.indexOf('rWrist') * 3);
  const weights = Array.from({ length: rig.boneCount }, (_, i) => i >= 4 && i < 8 ? 1 : 0);
  motion.update(0, false, 1);
  const destination = motion.deform(wrist.clone(), weights, 0);
  assert.ok(destination.y > wrist.y + 1, 'the arm must lift toward the camera');
  for (const time of [4, 9, 18, 28, 35, 79]) {
    motion.update(time, false, 1);
    assert.ok(motion.deform(wrist.clone(), weights, 0).distanceTo(destination) < 1e-6,
      'clicking during another gesture must still reach the same wrist position');
  }
});

test('the viewer-left arm lifts in one plane without elbow or wrist twists', () => {
  const motion = createBodyMotion(rig);
  for (const progress of [0, .25, .5, .75, 1]) {
    motion.update(0, true, progress);
    assert.ok(Math.abs(motion.rotations[4].x) < 1e-9);
    assert.ok(Math.abs(motion.rotations[4].y) < 1e-9);
    assert.deepEqual(motion.rotations.slice(5, 8).map(q => q.toArray()), Array.from({ length: 3 }, () => [0, 0, 0, 1]));
  }
});

test('the carried hand reaches up while the second hand reaches down from above', () => {
  const right = createReachingHandMotion(hand.rig, 1), left = createReachingHandMotion(hand.rig, 0);
  right.update(0, true); left.update(0, true);
  const matrix = raisedHandMatrix(rig), pair = createHandPairLayout(hand.rig);
  pair.update(matrix, right, left);
  for (const [motion, transform, direction] of [[right, matrix, 1], [left, pair.partnerMatrix, -1]] as const) {
    for (const finger of ['index']) {
      const start = hand.rig.jointNames.indexOf(finger + 'MCP');
      const positions = ['MCP', 'Tip'].map(suffix => {
        const weights = Array.from({ length: hand.rig.boneCount }, (_, i) => i === 0 || (i >= start && i < start + (suffix === 'Tip' ? 3 : 1)) ? 1 : 0);
        return motion.deform(new THREE.Vector3().fromArray(hand.rig.joints,
          hand.rig.jointNames.indexOf(finger + suffix) * 3), weights, 0).applyMatrix4(transform);
      });
      assert.ok((positions[1].y - positions[0].y) * direction > .01, 'the two index fingers should reach from opposite heights');
    }
  }
  assert.ok(pair.partnerMatrix.elements[13] > matrix.elements[13], 'the partner wrist should be higher than the carried wrist');
});

test('the camera establishes the hand close-up before the main arm lift and finishes gently', () => {
  const start = connectionTiming(0), early = connectionTiming(.1), focused = connectionTiming(.22);
  assert.equal(start.zoom, 0);
  assert.equal(start.focus, 0);
  assert.ok(connectionTiming(.001).zoom > 0);
  assert.ok(early.zoom > .55, 'the opening must zoom promptly onto the hand');
  assert.ok(early.focus > .8, 'the camera should already track the hand, not the full figure');
  assert.ok(early.lift < .01, 'establish the close-up before lifting at the shoulder');
  assert.equal(focused.focus, 1);
  assert.ok(connectionTiming(.55).lift - connectionTiming(.45).lift > early.lift * 20);
  assert.ok(connectionTiming(1).zoom - connectionTiming(.9).zoom < .003);
  assert.equal(connectionTiming(1).zoom, 1);
});

test('hand detail grows during the first second of zoom and retraces the same growth on return', () => {
  assert.equal(refinementFromZoom(connectionTiming(0).zoom), 0, 'preserve the untouched landing hand');
  const frames = [0, .18, .20, .35, .55, .70, 1];
  const forward = frames.map(refinementFromZoom);
  assert.equal(forward.at(-1), 1);
  assert.ok(forward[2] > 0 && forward[2] < .002, 'new detail must start growing gently');
  assert.ok(forward.every((value, i) => i === 0 || value >= forward[i - 1]));
  assert.ok(refinementFromZoom(connectionTiming(.1).zoom) > .85, 'the hand should already be detailed during the fast opening');
  assert.equal(refinementFromZoom(connectionTiming(.16).zoom), 1, 'finish refinement within the first second');
  assert.deepEqual([...frames].reverse().map(refinementFromZoom), [...forward].reverse());
});

test('forward and reverse use one timeline and wait for their destination route', () => {
  for (const p of [.1, .25, .5, .8]) {
    assert.ok(Math.abs(advanceConnection(advanceConnection(p, 200, 1, true), 200, -1, true) - p) < 1e-10);
  }
  assert.equal(advanceConnection(.7, 1000, 1, false), .72);
  assert.equal(advanceConnection(.3, 1000, -1, false), .28);
  assert.equal(advanceConnection(.3, 6000, -1, true), 0);
});

test('the denser arm shares source vertices with the body and keeps graph spacing consistent', () => {
  const body = JSON.parse(readFileSync(new URL('../public/models/human-exploration.json', import.meta.url), 'utf8'));
  hand.body.vertexIds.forEach((id, i) => assert.deepEqual(hand.body.positions.slice(i * 3, i * 3 + 3), body.positions.slice(id * 3, id * 3 + 3)));
  assert.ok(Math.max(...hand.spacing) / Math.min(...hand.spacing) < 1.5);
  assert.equal(hand.body.originalNodeIds.length * 3, hand.body.originalNodes.length);
  assert.equal(hand.body.growthNodeIds.length * 3, hand.nodes.length);
  hand.body.growthNodeIds.forEach(id => assert.ok(hand.body.nodeMask[id], 'each new sample must grow from an existing arm node'));
  const shoulder = new THREE.Vector3().fromArray(rig.joints, rig.jointNames.indexOf('rShoulder') * 3);
  const distances = hand.body.vertexIds.map(id => new THREE.Vector3().fromArray(body.positions, id * 3).distanceTo(shoulder));
  assert.ok(Math.min(...distances) < .22, 'the detailed mesh must extend through the upper arm to the shoulder cap');
});

test('the second hand has gentle ongoing motion without rotating the arm', () => {
  const right = createReachingHandMotion(hand.rig, 1), left = createReachingHandMotion(hand.rig, 0);
  right.update(0, true); left.update(0, true);
  const pair = createHandPairLayout(hand.rig), matrix = raisedHandMatrix(rig);
  pair.update(matrix, right, left, 1, 0);
  const initial = pair.partnerMatrix.clone();
  pair.update(matrix, right, left, 1, 2);
  assert.deepEqual(pair.partnerMatrix.elements.slice(0, 12), initial.elements.slice(0, 12));
  assert.ok(Math.abs(pair.partnerMatrix.elements[13] - initial.elements[13]) > .005);
  assert.ok(Math.abs(pair.partnerMatrix.elements[13] - initial.elements[13]) < .02);
});

test('the second hand enters by translation, preserving its orientation and the final fingertip gap', () => {
  const right = createReachingHandMotion(hand.rig, 1), left = createReachingHandMotion(hand.rig, 0);
  right.update(0, true); left.update(0, true);
  const matrix = raisedHandMatrix(rig), pair = createHandPairLayout(hand.rig);
  const rotations: number[][] = [];
  for (const entrance of [0, .25, .5, .75, 1]) {
    pair.update(matrix, right, left, entrance);
    rotations.push(pair.partnerMatrix.elements.slice(0, 12));
  }
  rotations.forEach(rotation => assert.deepEqual(rotation, rotations[0]));
  const weights = hand.rig.jointNames.slice(0, hand.rig.boneCount).map(name => ['wrist', 'indexMCP', 'indexPIP', 'indexDIP'].includes(name) ? 1 : 0);
  const tip = left.deform(new THREE.Vector3().fromArray(hand.rig.joints, hand.rig.jointNames.indexOf('indexTip') * 3), weights, 0).applyMatrix4(pair.partnerMatrix);
  assert.ok(Math.abs(pair.carrierTip.x - tip.x - HAND_GAP) < 1e-6);
});

test('the standalone About framing matches the attached, lifted body hand', () => {
  const motion = createBodyMotion(rig);
  motion.update(0, true, 1);
  const original = createHandAttachment(rig).matrix;
  const figure = new THREE.Matrix4().makeRotationFromEuler(FIGURE_ORIENTATION);
  const weights = Array.from({ length: rig.boneCount }, (_, i) => i >= 4 && i < 8 ? 1 : 0);
  for (const p of [new THREE.Vector3(), new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0)]) {
    const expected = p.clone().applyMatrix4(raisedHandMatrix(rig));
    const attached = motion.deform(p.clone().applyMatrix4(original), weights, 0).applyMatrix4(figure);
    assert.ok(attached.distanceTo(expected) < 1e-6);
  }
});

test('hand skinning stays finite while the fingers articulate from the body pose', () => {
  for (const side of [0, 1] as const) {
    const motion = createReachingHandMotion(hand.rig, side);
    for (const articulation of [0, .25, .5, .75, 1]) {
      motion.update(0, true, articulation);
      for (let i = 0; i < hand.positions.length / 3; i++) {
        const p = motion.deform(new THREE.Vector3().fromArray(hand.positions, i * 3), hand.rig.surfaceWeights, i * hand.rig.boneCount);
        assert.ok(p.toArray().every(Number.isFinite));
      }
    }
  }
});

test('the upper arm retains its length while the shoulder lifts', () => {
  const body = JSON.parse(readFileSync(new URL('../public/models/human-exploration.json', import.meta.url), 'utf8'));
  const shoulder = new THREE.Vector3().fromArray(rig.joints, rig.jointNames.indexOf('rShoulder') * 3);
  const elbow = new THREE.Vector3().fromArray(rig.joints, rig.jointNames.indexOf('rElbow') * 3);
  const axis = elbow.clone().sub(shoulder), length = axis.length(); axis.normalize();
  const points: THREE.Vector3[] = body.spacing.map((_: number, i: number) => new THREE.Vector3().fromArray(body.nodes, i * 3));
  const upperArm = (p: THREE.Vector3) => {
    const offset = p.clone().sub(shoulder), along = offset.dot(axis) / length;
    return along > .45 && along < .95 && offset.addScaledVector(axis, -along * length).length() < .24;
  };
  const motion = createBodyMotion(rig);
  for (const lift of [.25, .5, .75, 1]) {
    motion.update(0, true, lift);
    const posed = points.map((p, i) => motion.deform(p.clone(), rig.nodeWeights, i * rig.boneCount));
    let checked = 0;
    for (let i = 0; i < body.edges.length; i += 2) {
      const a = body.edges[i], b = body.edges[i + 1];
      if (!upperArm(points[a]) || !upperArm(points[b])) continue;
      const ratio = posed[a].distanceTo(posed[b]) / points[a].distanceTo(points[b]);
      assert.ok(ratio > .92 && ratio < 1.08, `upper-arm connection stretched by ${ratio}`);
      checked++;
    }
    assert.ok(checked > 25);
  }
});

test('refining nodes branch from the coarse graph and their edges remain connected', () => {
  const graph = createConnectionGraph({
    positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], triangles: [0, 1, 2],
    nodes: [0, 0, 0, 1, 0, 0], normals: [0, 0, 1, 0, 0, 1], spacing: [.08, .08], edges: [0, 1],
  }, true);
  const matrix = new THREE.Matrix4(), midpoint = new THREE.Vector3();
  try {
    for (const growth of [0, .25, .5, .75, 1]) {
      graph.setGrowth(growth);
      graph.update(p => { p.y += 2; }, p => { p.set(.5, 2, .008); });
      const a = graph.nodePosition(0), b = graph.nodePosition(1);
      assert.ok(Math.abs(a.distanceTo(b) - growth) < 1e-8);
      assert.equal(a.y, 2);
      const lines = graph.group.children[1] as THREE.InstancedMesh;
      lines.getMatrixAt(0, matrix); midpoint.setFromMatrixPosition(matrix);
      const edgeLength = new THREE.Vector3(0, -.5, 0).applyMatrix4(matrix).distanceTo(new THREE.Vector3(0, .5, 0).applyMatrix4(matrix));
      assert.ok(Math.abs(edgeLength - a.distanceTo(b)) < 1e-7, 'edges span the growing endpoints, without disconnected segments');
      assert.ok(midpoint.distanceTo(a.clone().add(b).multiplyScalar(.5)) < 1e-7);
    }
  } finally { graph.dispose(); }
});
