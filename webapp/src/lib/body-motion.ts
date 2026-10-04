import * as THREE from 'three';

export type HumanRig = {
  boneCount: number;
  jointNames: string[];
  joints: number[];
  surfaceWeights: number[];
  nodeWeights: number[];
};

const ease = THREE.MathUtils.smoothstep;
const envelope = (time: number, start: number, arrive: number, leave: number, end: number) =>
  ease(time, start, arrive) * (1 - ease(time, leave, end));
const identity = new THREE.Quaternion();
export const MISSION_ARM_LIFT = -1.62;

/** A quiet sequence of anatomically limited, curious movements. */
export function createBodyMotion(rig: HumanRig) {
  const joint = (name: string) => {
    const index = rig.jointNames.indexOf(name);
    if (index < 0) throw new Error(`Missing human rig joint: ${name}`);
    return new THREE.Vector3().fromArray(rig.joints, index * 3);
  };
  const pivots = [
    joint('lShoulder'), joint('lElbow'), joint('lElbow'), joint('lWrist'),
    joint('rShoulder'), joint('rElbow'), joint('rElbow'), joint('rWrist'),
    joint('neck'), joint('lHip'), joint('lKnee'), joint('lAnkle'),
    joint('rHip'), joint('rKnee'), joint('rAnkle'),
  ];
  const rotations = pivots.map(() => new THREE.Quaternion());
  const matrices = pivots.map(() => new THREE.Matrix4());
  const axes = pivots.map(() => new THREE.Vector3());
  const angles = new Float64Array(rig.boneCount);
  const offset = new THREE.Vector3();
  const xAxis = new THREE.Vector3(1, 0, 0);
  const zAxis = new THREE.Vector3(0, 0, 1);
  const euler = new THREE.Euler(0, 0, 0, 'YXZ');

  function armPose(side: 'l' | 'r', open = false) {
    const sign = side === 'l' ? 1 : -1;
    const shoulder = joint(`${side}Shoulder`), elbow = joint(`${side}Elbow`), wrist = joint(`${side}Wrist`);
    const upper = elbow.clone().sub(shoulder), lower = wrist.clone().sub(elbow);
    const target = new THREE.Vector3(sign * (open ? 1.14 : .86), open ? 1.06 : 1.86, open ? .84 : 1.04);
    const reach = target.clone().sub(shoulder);
    const distance = reach.length();
    reach.normalize();
    const along = (upper.lengthSq() - lower.lengthSq() + distance * distance) / (2 * distance);
    const pole = new THREE.Vector3(sign * 1.65, 1.00, .38).sub(shoulder);
    pole.addScaledVector(reach, -pole.dot(reach)).normalize();
    const elbowTarget = shoulder.clone().addScaledVector(reach, along).addScaledVector(pole, Math.sqrt(Math.max(0, upper.lengthSq() - along * along)));
    const shoulderRotation = new THREE.Quaternion().setFromUnitVectors(upper.clone().normalize(), elbowTarget.clone().sub(shoulder).normalize());
    const lowerTarget = target.clone().sub(elbowTarget).applyQuaternion(shoulderRotation.clone().invert()).normalize();
    const elbowRotation = new THREE.Quaternion().setFromUnitVectors(lower.clone().normalize(), lowerTarget);

    // The thumb-side direction crossed with the fingers gives the actual
    // palmar normal. Its opposite is the back of the hand.
    const fingers = joint(`${side}MiddleTip`).sub(wrist).normalize();
    const across = joint(`${side}IndexBase`).sub(joint(`${side}PinkyBase`)).normalize();
    const palm = across.clone().cross(fingers).normalize().multiplyScalar(sign);
    const armRotation = shoulderRotation.clone().multiply(elbowRotation);
    const forearmAxis = lower.clone().normalize();
    const worldAxis = forearmAxis.clone().applyQuaternion(armRotation);
    const currentPalm = palm.applyQuaternion(armRotation);
    const eye = joint('head').add(new THREE.Vector3(0, .04, .36));
    const desiredPalm = eye.sub(target).normalize();
    currentPalm.addScaledVector(worldAxis, -currentPalm.dot(worldAxis)).normalize();
    desiredPalm.addScaledVector(worldAxis, -desiredPalm.dot(worldAxis)).normalize();
    const twist = Math.atan2(worldAxis.dot(currentPalm.clone().cross(desiredPalm)), currentPalm.dot(desiredPalm));
    // Supination is distributed from elbow to hand, never concentrated at
    // the wrist. Wrist movement is a small flex, with no axial spin.
    const forearmRotation = new THREE.Quaternion().setFromAxisAngle(forearmAxis, THREE.MathUtils.clamp(twist, -.95, .95));
    const wristRotation = new THREE.Quaternion().setFromAxisAngle(across, sign * (open ? .06 : .12));
    return [shoulderRotation, elbowRotation, forearmRotation, wristRotation];
  }

  const leftFocus = armPose('l');
  const leftOpen = armPose('l', true);
  const order = [3, 2, 1, 0, 7, 6, 5, 4, 11, 10, 9, 14, 13, 12, 8];
  const focusRegions = [
    { name: 'left-hand', center: joint('lWrist').lerp(joint('lMiddleBase'), .65), radius: .27, bones: [3, 2, 1, 0] },
    { name: 'right-hand', center: joint('rWrist').lerp(joint('rMiddleBase'), .65), radius: .27, bones: [7, 6, 5, 4] },
    { name: 'left-leg', center: joint('lKnee').lerp(joint('lAnkle'), .12), radius: .42, bones: [10, 9] },
    { name: 'right-leg', center: joint('rKnee').lerp(joint('rAnkle'), .12), radius: .42, bones: [13, 12] },
  ];
  const eye = joint('head').add(new THREE.Vector3(0, .04, .36));
  const gaze = new THREE.Vector3();
  const gazeRotation = new THREE.Quaternion();
  const state = { gesture: 'floating', sway: 0, lean: 0, focusRegion: 0, focusStrength: 0 };

  function update(time: number, reducedMotion = false, reaching = 0) {
    const t = time % 40;
    const left = reducedMotion ? 0 : envelope(t, .6, 4.5, 6.8, 10.4);
    const right = reducedMotion ? 0 : envelope(t, 6.4, 10.4, 12.2, 16.4);
    const leftFoot = reducedMotion ? 0 : envelope(t, 16.0, 20.2, 22.2, 26.4);
    const open = reducedMotion ? 0 : envelope(t, 25.0, 28.8, 31.0, 35.0);
    const rightFoot = reducedMotion ? 0 : envelope(t, 29.0, 33.0, 34.5, 38.8);
    const breathe = reducedMotion ? 0 : Math.sin(time * .55) * .007;

    for (let i = 0; i < 4; i++) {
      rotations[i].copy(identity).slerp(leftFocus[i], left).slerp(leftOpen[i], open * .68);
    }
    // The viewer's left is the figure's anatomical right. This arm only
    // lifts at the shoulder in the image plane; it never rolls at the wrist.
    rotations[4].setFromAxisAngle(zAxis, THREE.MathUtils.lerp(-.12 * right - .15 * open, MISSION_ARM_LIFT, reaching));
    rotations[5].identity(); rotations[6].identity(); rotations[7].identity();
    const scan = reducedMotion ? 0 : Math.sin(time * .23) * .20 * (1 - Math.max(left, right));
    euler.set(.30 * Math.max(left, right) + .43 * Math.max(leftFoot, rightFoot) + .15 * open + breathe, .32 * (left - right) + scan + .08 * (leftFoot - rightFoot), .035 * (right - left));
    rotations[8].setFromEuler(euler);
    rotations[9].setFromEuler(euler.set(-.80 * leftFoot, .04 * leftFoot, -.065 * leftFoot));
    rotations[10].setFromAxisAngle(xAxis, 1.50 * leftFoot);
    rotations[11].setFromAxisAngle(xAxis, -.21 * leftFoot);
    rotations[12].setFromEuler(euler.set(-.65 * rightFoot, -.06 * rightFoot, .065 * rightFoot));
    rotations[13].setFromAxisAngle(xAxis, 1.28 * rightFoot);
    rotations[14].setFromAxisAngle(xAxis, -.15 * rightFoot);

    // Exclusive windows fade fully out before the next region lights up.
    // Gaze and color use this same focus, including the second hand gesture.
    state.focusRegion = 0;
    state.focusStrength = 0;
    if (!reducedMotion && reaching === 0) {
      if (t < 8.6) { state.focusRegion = 1; state.focusStrength = envelope(t, .6, 4.5, 6.8, 8.6); }
      else if (t < 16.4) { state.focusRegion = 2; state.focusStrength = envelope(t, 8.6, 10.4, 12.2, 16.4); }
      else if (t < 26.4) { state.focusRegion = 3; state.focusStrength = envelope(t, 16.4, 20.2, 22.2, 26.4); }
      else if (t < 31) { state.focusRegion = 1; state.focusStrength = envelope(t, 26.4, 28.8, 29.4, 31); }
      else { state.focusRegion = 4; state.focusStrength = envelope(t, 31, 33, 34.5, 38.8); }
    }
    if (state.focusStrength > 0) {
      const region = focusRegions[state.focusRegion - 1];
      gaze.copy(region.center);
      for (const bone of region.bones) gaze.sub(pivots[bone]).applyQuaternion(rotations[bone]).add(pivots[bone]);
      gaze.sub(eye);
      const pitch = THREE.MathUtils.clamp(Math.atan2(-gaze.y, Math.hypot(gaze.x, gaze.z)), 0, .65);
      const yaw = THREE.MathUtils.clamp(Math.atan2(gaze.x, Math.max(.15, gaze.z)), -.55, .55);
      rotations[8].slerp(gazeRotation.setFromEuler(euler.set(pitch, yaw, 0)), state.focusStrength);
    } else state.focusRegion = 0;

    for (let i = 0; i < rotations.length; i++) {
      const q = rotations[i];
      const magnitude = Math.hypot(q.x, q.y, q.z);
      angles[i] = 2 * Math.atan2(magnitude, q.w);
      axes[i].set(q.x, q.y, q.z).divideScalar(magnitude || 1);
      matrices[i].makeRotationFromQuaternion(q);
      offset.copy(pivots[i]).applyQuaternion(q).multiplyScalar(-1).add(pivots[i]);
      matrices[i].setPosition(offset);
    }
    state.gesture = left > .5 ? 'examining-left-palm' : right > .5 ? 'examining-right-palm' : leftFoot > .4 ? 'lifting-left-foot' : rightFoot > .4 ? 'lifting-right-foot' : open > .4 ? 'exploring-hands' : 'looking-around';
    state.sway = (leftFoot - rightFoot) * .055;
    state.lean = (leftFoot - rightFoot) * .027;
    return state;
  }

  function deform(point: THREE.Vector3, weights: number[], start: number) {
    for (const bone of order) {
      const weight = weights[start + bone];
      if (weight <= 0 || angles[bone] === 0) continue;
      if (weight >= .999) { point.applyMatrix4(matrices[bone]); continue; }
      // Interpolate rotation angles, not vertex positions. Linear blending
      // of a large rotation collapses the wrist and elbow into a pinched knot.
      const pivot = pivots[bone], axis = axes[bone];
      const x = point.x - pivot.x, y = point.y - pivot.y, z = point.z - pivot.z;
      const angle = angles[bone] * weight, c = Math.cos(angle), s = Math.sin(angle);
      const dot = axis.x * x + axis.y * y + axis.z * z;
      point.set(
        pivot.x + x * c + (axis.y * z - axis.z * y) * s + axis.x * dot * (1 - c),
        pivot.y + y * c + (axis.z * x - axis.x * z) * s + axis.y * dot * (1 - c),
        pivot.z + z * c + (axis.x * y - axis.y * x) * s + axis.z * dot * (1 - c),
      );
    }
    return point;
  }

  return { update, deform, rotations, focusRegions };
}
