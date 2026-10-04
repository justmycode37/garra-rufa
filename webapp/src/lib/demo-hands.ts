import * as THREE from 'three';
import type { HumanRig } from './body-motion';
import { createConnectionGraph } from './connection-graph';
import { createHandPairLayout, HAND_SCALE, raisedHandMatrix } from './hand-layout';
import { createReachingHandMotion, isReachingHandGraph } from './reaching-hand-motion';
import { FILM_CUES, finalePose, handApproachPose } from './demo-film';

export type DemoHands = { render: (time: number, reduced: boolean) => void; dispose: () => void };

/** The actual About geometry, pose, graph renderer and fingertip layout. */
export async function createDemoHands(canvas: HTMLCanvasElement): Promise<DemoHands> {
  const [body, hand] = await Promise.all([
    fetch('/models/human-exploration.json?v=shoulder-socket-2').then(response => {
      if (!response.ok) throw new Error('Body geometry unavailable');
      return response.json() as Promise<{ rig: HumanRig }>;
    }),
    fetch('/models/reaching-hands.json?v=connected-upper-arm-5').then(response => {
      if (!response.ok) throw new Error('Hand geometry unavailable');
      return response.json();
    }),
  ]);
  if (!isReachingHandGraph(hand)) throw new Error('Hand geometry version mismatch');

  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setClearColor(0xffffff, 0);
  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-2, 2, 1, -1, .01, 100);
  const carrier = createConnectionGraph(hand, true);
  const partner = createConnectionGraph(hand, true);
  carrier.group.matrixAutoUpdate = partner.group.matrixAutoUpdate = false;
  scene.add(carrier.group, partner.group);
  const carrierMotion = createReachingHandMotion(hand.rig, 1);
  const partnerMotion = createReachingHandMotion(hand.rig, 0);
  const carrierMatrix = raisedHandMatrix(body.rig);
  const pair = createHandPairLayout(hand.rig);
  carrierMotion.update(0, true);
  partnerMotion.update(0, true);
  pair.update(carrierMatrix, carrierMotion, partnerMotion);
  const center = pair.center.clone();
  const direction = Math.sign(carrierMatrix.elements[12] - center.x) || 1;
  const worldWidth = 8.7 * HAND_SCALE;
  const turning = new THREE.Matrix4();
  const approach = (matrix: THREE.Matrix4, rest: THREE.Matrix4, angle: number, dx: number, dy: number) => {
    const x = rest.elements[12], y = rest.elements[13];
    turning.makeRotationZ(angle);
    turning.setPosition(x - Math.cos(angle) * x + Math.sin(angle) * y, y - Math.sin(angle) * x - Math.cos(angle) * y, 0);
    matrix.multiplyMatrices(turning, rest);
    matrix.elements[12] += dx;
    matrix.elements[13] += dy;
  };
  let width = 0, height = 0;

  return {
    render(time, reduced) {
      const pose = finalePose(time);
      const entrance = pose.hands;
      canvas.style.opacity = String(pose.handsOpacity);
      canvas.style.visibility = entrance > 0 && pose.handsOpacity > 0 ? 'visible' : 'hidden';
      if (entrance === 0 || pose.handsOpacity === 0) return;
      const nextWidth = Math.max(1, canvas.clientWidth), nextHeight = Math.max(1, canvas.clientHeight);
      if (nextWidth !== width || nextHeight !== height) {
        width = nextWidth; height = nextHeight;
        renderer.setSize(width, height, false);
        camera.left = -worldWidth / 2; camera.right = worldWidth / 2;
        camera.top = worldWidth / 2 * height / width; camera.bottom = -camera.top;
        camera.position.set(center.x, center.y, 20);
        camera.updateProjectionMatrix();
      }
      const handTime = Math.max(0, time - FILM_CUES.hands.start);
      carrierMotion.update(handTime, reduced, 1, entrance);
      partnerMotion.update(handTime, reduced, 1, entrance);
      const movement = handApproachPose(time);
      // Open the fingers and unroll the wrists as the forearms lift toward the gap.
      for (const motion of [carrierMotion, partnerMotion]) {
        motion.angles[0] += movement.curl * .7;
        for (let index = 4; index < hand.rig.boneCount; index++) motion.angles[index] -= movement.curl * (index <= 6 ? .5 : 1);
      }
      pair.update(carrierMatrix, carrierMotion, partnerMotion, 1, reduced ? 0 : handTime);
      const travel = movement.travel * worldWidth;
      approach(carrier.group.matrix, carrierMatrix, -direction * movement.turn, direction * travel, -movement.lift);
      approach(partner.group.matrix, pair.partnerMatrix, direction * movement.turn * .85, -direction * travel, movement.lift * .8);
      carrier.update((point, index, surface) => carrierMotion.deform(point, surface ? hand.rig.surfaceWeights : hand.rig.nodeWeights, index * hand.rig.boneCount));
      partner.update((point, index, surface) => partnerMotion.deform(point, surface ? hand.rig.surfaceWeights : hand.rig.nodeWeights, index * hand.rig.boneCount));
      renderer.render(scene, camera);
      canvas.dataset.entrance = entrance.toFixed(3);
    },
    dispose() {
      carrier.dispose(); partner.dispose(); renderer.dispose();
    },
  };
}
