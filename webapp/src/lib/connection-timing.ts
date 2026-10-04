import { MathUtils } from 'three';

export const CONNECTION_DURATION = 6000;

export function connectionTiming(progress: number) {
  // The arm eases into its lift. The camera establishes a hand close-up first,
  // then completes its remaining travel with a quiet, gradual finish.
  const shot = MathUtils.smootherstep(progress, 0, 1);
  const approach = 1 - Math.pow(1 - Math.min(progress / .28, 1), 3);
  return {
    lift: MathUtils.smoothstep(shot, 0, .62),
    zoom: .78 * approach + .22 * shot,
    focus: 1 - Math.pow(1 - Math.min(progress / .22, 1), 3),
    shape: MathUtils.smoothstep(shot, .04, .50),
    entrance: MathUtils.smoothstep(shot, .42, .92),
  };
}

/** Grow during the opening zoom, reaching full detail before the arm lifts. */
export function refinementFromZoom(zoom: number) {
  return MathUtils.smootherstep(zoom, .18, .70);
}

export function advanceConnection(progress: number, deltaMs: number, direction: 1 | -1, destinationReady: boolean) {
  const next = MathUtils.clamp(progress + direction * deltaMs / CONNECTION_DURATION, 0, 1);
  return destinationReady ? next : direction === 1 ? Math.min(next, .72) : Math.max(next, .28);
}
