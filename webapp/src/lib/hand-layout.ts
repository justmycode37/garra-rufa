import * as THREE from 'three';
import { MISSION_ARM_LIFT, type HumanRig } from './body-motion';
import type { ReachingHandRig } from './reaching-hand-motion';

export const HAND_SCALE = .36;
export const HAND_GAP = .18 * HAND_SCALE;
export const FIGURE_ORIENTATION = new THREE.Euler(.025, -.26, -.016);
type HandPose = { deform: (p: THREE.Vector3, weights: ArrayLike<number>, start: number) => THREE.Vector3 };

/** The viewer-left hand rests palm-down from its very first frame. */
export function createHandAttachment(rig: HumanRig) {
  const joint = (name: string) => new THREE.Vector3().fromArray(rig.joints, rig.jointNames.indexOf(name) * 3);
  const wrist = joint('rWrist');
  const forward = joint('rMiddleTip').sub(wrist).normalize();
  const radial = joint('rIndexBase').sub(joint('rPinkyBase'));
  radial.addScaledVector(forward, -radial.dot(forward)).normalize();
  const palmar = new THREE.Vector3().crossVectors(forward, radial);
  const matrix = new THREE.Matrix4().makeBasis(forward, radial, palmar).multiply(new THREE.Matrix4().makeRotationX(.35))
    .scale(new THREE.Vector3(HAND_SCALE, HAND_SCALE, HAND_SCALE)).setPosition(wrist);
  return { matrix, wrist };
}

/** Used by the static artwork and regression tests; matches the live lift. */
export function raisedHandMatrix(rig: HumanRig) {
  const shoulder = new THREE.Vector3().fromArray(rig.joints, rig.jointNames.indexOf('rShoulder') * 3);
  const lift = new THREE.Matrix4().makeTranslation(shoulder.x, shoulder.y, shoulder.z)
    .multiply(new THREE.Matrix4().makeRotationZ(MISSION_ARM_LIFT))
    .multiply(new THREE.Matrix4().makeTranslation(-shoulder.x, -shoulder.y, -shoulder.z));
  return new THREE.Matrix4().makeRotationFromEuler(FIGURE_ORIENTATION).multiply(lift).multiply(createHandAttachment(rig).matrix);
}

/** Anchor the other hand to the carried fingertip using translation alone. */
export function createHandPairLayout(rig: ReachingHandRig) {
  const index = new THREE.Vector3().fromArray(rig.joints, rig.jointNames.indexOf('indexTip') * 3);
  const weights = rig.jointNames.slice(0, rig.boneCount).map(name => ['wrist', 'indexMCP', 'indexPIP', 'indexDIP'].includes(name) ? 1 : 0);
  const carrierTip = new THREE.Vector3(), partnerTip = new THREE.Vector3(), center = new THREE.Vector3();
  const partnerMatrix = new THREE.Matrix4();
  // Keep the arm's orientation steady while its wrist and fingers articulate.
  // Re-anchor the deformed fingertip so the finishing gap stays consistent.
  const reflection = new THREE.Matrix4().makeRotationZ(-.30).multiply(new THREE.Matrix4().makeScale(-1, 1, 1));
  return { carrierTip, partnerMatrix, center, update(carrierMatrix: THREE.Matrix4, carrier: HandPose, partner: HandPose, entrance = 1, time = 0) {
    carrier.deform(carrierTip.copy(index), weights, 0).applyMatrix4(carrierMatrix);
    partnerMatrix.multiplyMatrices(reflection, carrierMatrix).setPosition(0, 0, 0);
    partner.deform(partnerTip.copy(index), weights, 0).applyMatrix4(partnerMatrix);
    partnerMatrix.setPosition(carrierTip.x - HAND_GAP - (1 - entrance) * 4.5 * HAND_SCALE - partnerTip.x + Math.sin(time * .52) * .012 * HAND_SCALE,
      carrierTip.y + .06 * HAND_SCALE + (1 - entrance) * .1 * HAND_SCALE - partnerTip.y + Math.sin(time * .65) * .035 * HAND_SCALE,
      carrierTip.z - partnerTip.z);
    center.copy(carrierTip); center.x -= HAND_GAP / 2;
  } };
}
