export const FILM_LEAD_IN = 2;
export const FILM_DURATION = 28;
export const filmContentTime = (elapsed: number) => elapsed - FILM_LEAD_IN;
export const OPENING_COPY = 'To understand, means to connect the dots';
export const CLOSING_COPY = 'hard problems, require teamwork, to be solved';
export const NARRATION_COPY = 'But not just dots, data, researchers, symptoms, patients, protein structures, doctors, papers, context, everything you need, in one place.';

export const BRAND_CUES = {
  clearHands: { start: 10.12, end: 10.5 },
  dots: { start: 10.5, end: 12 },
  blank: { start: 12, end: 22 },
  jump: { start: 22, end: 23.15 },
  return: { start: 23.15, end: 24.4 },
  wordmark: { start: 23.25, end: 23.72 },
  splash: { start: 23.18, end: 24.65 },
} as const;

// Preserve the original ten-second opening; append the narration and brand ending.
export const FILM_CUES = {
  openingOut: 1.17,
  openingClear: 1.49,
  formStart: 1.5,
  formed: 2.1,
  leadMove: 2.17,
  leadArrive: 2.66,
  hardProblems: { start: 2.7, end: 3.15 },
  join: 3.48,
  cruise: 4.18,
  teamwork: { start: 4.45, end: 4.98 },
  solved: { start: 5.24, end: 5.77 },
  departure: 5.25,
  clear: 6.12,
  jump: { start: 6.13, end: 7.02 },
  white: { start: 6.8, end: 7.16 },
  question: { start: 7.24, end: 7.86 },
  connect: { start: 8.25, end: BRAND_CUES.clearHands.start },
  hands: { start: 8.25, end: 10 },
} as const;

export const clamp = (value: number) => Math.max(0, Math.min(1, value));
export const progress = (time: number, start: number, end: number) => clamp((time - start) / (end - start));
export const ease = (value: number) => value * value * (3 - 2 * value);
export const out = (value: number) => 1 - (1 - value) ** 3;
export const mix = (from: number, to: number, amount: number) => from + (to - from) * amount;

// The same three-triangle fish used throughout the app, enlarged for the film.
export const FISH_NODES = [
  { x: 75, y: -120, fromX: -560, fromY: -210, at: .28 },
  { x: 75, y: 120, fromX: 550, fromY: 220, at: .4 },
  { x: -135, y: 0, fromX: -605, fromY: 100, at: .54 },
  { x: 285, y: 0, fromX: 590, fromY: -120, at: .7 },
  { x: -270, y: -60, fromX: -320, fromY: 315, at: .81 },
  { x: -270, y: 60, fromX: 310, fromY: -300, at: .9 },
] as const;

export const FISH_EDGES = [
  { from: 0, to: 1, at: 1.51 },
  { from: 0, to: 2, at: 1.57 },
  { from: 2, to: 1, at: 1.64 },
  { from: 0, to: 3, at: 1.7 },
  { from: 3, to: 1, at: 1.77 },
  { from: 2, to: 4, at: 1.82 },
  { from: 4, to: 5, at: 1.87 },
  { from: 5, to: 2, at: 1.92 },
] as const;

const SCHOOL = [
  { x: 1600 * 2 / 3, y: 330, scale: .36, join: FILM_CUES.leadMove },
  { x: 780, y: 230, scale: .29, join: FILM_CUES.join },
  { x: 760, y: 445, scale: .32, join: FILM_CUES.join + .09 },
  { x: 490, y: 295, scale: .26, join: FILM_CUES.join + .18 },
  { x: 460, y: 510, scale: .28, join: FILM_CUES.join + .27 },
] as const;

export function textPose(time: number, start: number, end: number) {
  const entrance = out(progress(time, start, start + .3));
  const exit = ease(progress(time, end, end + .26));
  return { opacity: entrance * (1 - exit), x: (1 - entrance) * 46 - exit * 56 };
}

export function nodePose(time: number, index: number) {
  const point = FISH_NODES[index];
  const settling = ease(progress(time, FILM_CUES.formStart, FILM_CUES.formed));
  const visible = out(progress(time, point.at, point.at + .2));
  return {
    x: mix(point.fromX, point.x, settling),
    y: mix(point.fromY, point.y, settling),
    radius: visible * mix(7, 4.8, settling),
    opacity: visible,
  };
}

export function fishPose(time: number, index: number) {
  const fish = SCHOOL[index];
  const joining = ease(progress(time, fish.join, index === 0 ? FILM_CUES.leadArrive : fish.join + .4));
  const leaving = progress(time, FILM_CUES.departure + index * .04, FILM_CUES.clear);
  const travel = leaving * leaving;
  const cruising = ease(progress(time, FILM_CUES.cruise, FILM_CUES.departure)) * 120;
  const swimming = progress(time, FILM_CUES.leadMove, FILM_CUES.leadArrive);
  const wave = Math.sin(time * 5.6 - index * .85);
  return {
    x: mix(index === 0 ? 800 : -250 - index * 35, fish.x, joining) + cruising + travel * 1850,
    y: (index === 0 ? mix(450, fish.y, ease(progress(time, FILM_CUES.join, FILM_CUES.cruise))) : mix(fish.y + 45, fish.y, joining)) + wave * 11 * swimming - travel * 110,
    scale: index === 0 ? mix(1.06, fish.scale, joining) : fish.scale,
    rotation: (wave * 2.4 - travel * 10) * swimming,
    opacity: index === 0 ? 1 : progress(time, fish.join, fish.join + .16),
    tail: Math.sin(time * 14 - index) * 7 * swimming,
  };
}

export function finalePose(time: number) {
  const jump = progress(time, FILM_CUES.jump.start, FILM_CUES.jump.end);
  return {
    // The existing fish animation lands at 76% of its 1.5-second cycle.
    jumpAnimationTime: jump * 1500 * .76,
    jumpOpacity: time < FILM_CUES.jump.start ? 0 : 1 - progress(time, FILM_CUES.jump.end, FILM_CUES.jump.end + .07),
    white: ease(progress(time, FILM_CUES.white.start, FILM_CUES.white.end)),
    hands: ease(progress(time, FILM_CUES.hands.start, FILM_CUES.hands.end)),
    handsOpacity: 1 - ease(progress(time, BRAND_CUES.clearHands.start, BRAND_CUES.clearHands.end)),
  };
}

export function handApproachPose(time: number) {
  const entrance = finalePose(time).hands;
  const remaining = 1 - entrance;
  return { entrance, travel: remaining * .55, lift: remaining * .3, turn: remaining * .2, curl: remaining * .18 };
}

export function brandClosingPose(time: number) {
  const pairVisible = time < BRAND_CUES.dots.start ? 0 : 1 - ease(progress(time, BRAND_CUES.dots.end - .18, BRAND_CUES.dots.end));
  const returning = progress(time, BRAND_CUES.return.start, BRAND_CUES.return.end);
  const splash = progress(time, BRAND_CUES.splash.start, BRAND_CUES.splash.end);
  const splashFinished = time >= BRAND_CUES.splash.end;
  return {
    pairOpacity: pairVisible,
    firstDot: out(progress(time, BRAND_CUES.dots.start, BRAND_CUES.dots.start + .2)),
    secondDot: out(progress(time, BRAND_CUES.dots.start + .2, BRAND_CUES.dots.start + .4)),
    connection: out(progress(time, BRAND_CUES.dots.start + .45, BRAND_CUES.dots.end - .38)),
    jumpOpacity: time >= BRAND_CUES.jump.start && time < BRAND_CUES.jump.end ? 1 : 0,
    jumpAnimationTime: progress(time, BRAND_CUES.jump.start, BRAND_CUES.jump.end) * 1500 * .76,
    fishOpacity: out(progress(time, BRAND_CUES.return.start, BRAND_CUES.return.start + .1)),
    fishTravel: ease(returning),
    fishArc: returning === 1 ? 0 : Math.sin(Math.PI * returning) ** 2,
    fishRotation: returning < .55 ? mix(-38, 12, ease(returning / .55)) : mix(12, 0, ease((returning - .55) / .45)),
    wordmarkOpacity: out(progress(time, BRAND_CUES.wordmark.start, BRAND_CUES.wordmark.end)),
    splashOpacity: time < BRAND_CUES.splash.start || splashFinished ? 0 : out(progress(time, BRAND_CUES.splash.start, BRAND_CUES.splash.start + .08)),
    splashTravel: splash,
    splashArc: 4 * splash * (1 - splash),
    periodOpacity: splashFinished ? 1 : 0,
    dropletsOpacity: time < BRAND_CUES.splash.start ? 0 : 1 - progress(time, BRAND_CUES.splash.start + .04, BRAND_CUES.splash.start + .38),
    dropletsTravel: progress(time, BRAND_CUES.splash.start, BRAND_CUES.splash.start + .38),
  };
}

export type BrandLayout = {
  fishWidth: number; fishX: number; fishY: number;
  wordmarkLeft: number; wordmarkTop: number;
  periodX: number; periodY: number; periodSize: number;
};

export function brandFishPosition(time: number, layout: BrandLayout) {
  const pose = brandClosingPose(time);
  // Reserve enough room for the full rotated SVG, not just its unrotated width.
  const radius = Math.hypot(layout.fishWidth / 2, layout.fishWidth / 4);
  const x = Math.min(layout.fishX, layout.wordmarkLeft - Math.max(8, layout.fishWidth * .06) - radius);
  return {
    x,
    y: mix(layout.fishY + layout.fishWidth * .7, layout.fishY, pose.fishTravel) - pose.fishArc * Math.min(155, layout.fishWidth * .95),
    rotation: pose.fishRotation,
    surfaceY: layout.fishY + layout.fishWidth * .4,
  };
}

export function brandSplashPosition(time: number, layout: BrandLayout) {
  const pose = brandClosingPose(time);
  const fish = brandFishPosition(time, layout);
  const crest = layout.wordmarkTop - Math.max(24, layout.fishWidth * .45);
  const t = pose.splashTravel;
  // Rise beside the fish, travel above the letters, then fall into the period.
  if (t < .22) return { x: fish.x, y: mix(fish.surfaceY, crest, ease(t / .22)) };
  if (t < .78) {
    const travel = (t - .22) / .56;
    return { x: mix(fish.x, layout.periodX, ease(travel)), y: crest - Math.sin(Math.PI * travel) ** 2 * layout.fishWidth * .12 };
  }
  return { x: layout.periodX, y: mix(crest, layout.periodY, ease((t - .78) / .22)) };
}
