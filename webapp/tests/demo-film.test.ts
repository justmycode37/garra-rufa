import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BRAND_CUES, CLOSING_COPY, FILM_CUES, FILM_DURATION, FILM_LEAD_IN, FISH_EDGES, FISH_NODES, NARRATION_COPY, OPENING_COPY, brandClosingPose, brandFishPosition, brandSplashPosition, filmContentTime, finalePose, fishPose, handApproachPose, nodePose, textPose, type BrandLayout } from '../src/lib/demo-film';

test('the expanded film preserves the original sequence and reserves the requested pauses', () => {
  assert.equal(FILM_DURATION, 28);
  assert.equal(FILM_LEAD_IN, 2);
  assert.equal(FILM_CUES.hands.end, 10);
  assert.equal(BRAND_CUES.dots.end - BRAND_CUES.dots.start, 1.5);
  assert.equal(BRAND_CUES.blank.end - BRAND_CUES.blank.start, 10);
  assert.equal(OPENING_COPY, 'To understand, means to connect the dots');
  assert.equal(CLOSING_COPY, 'hard problems, require teamwork, to be solved');
  assert.equal(NARRATION_COPY, 'But not just dots, data, researchers, symptoms, patients, protein structures, doctors, papers, context, everything you need, in one place.');
});

test('dots surround the opening text and only connect after it disappears', () => {
  for (let index = 0; index < FISH_NODES.length; index++) {
    const surrounding = nodePose(FILM_CUES.openingOut - .02, index);
    assert.equal(surrounding.opacity, 1);
    assert.equal(surrounding.x, FISH_NODES[index].fromX);
    assert.equal(surrounding.y, FISH_NODES[index].fromY);
    const formed = nodePose(FILM_CUES.formed, index);
    assert.equal(formed.x, FISH_NODES[index].x);
    assert.equal(formed.y, FISH_NODES[index].y);
  }
  assert.ok(FISH_EDGES.every(edge => edge.at > FILM_CUES.openingClear));
  const connected = new Set([0]);
  for (let pass = 0; pass < FISH_NODES.length; pass++) {
    for (const edge of FISH_EDGES) {
      if (connected.has(edge.from)) connected.add(edge.to);
      if (connected.has(edge.to)) connected.add(edge.from);
    }
  }
  assert.equal(connected.size, FISH_NODES.length);
});

test('the leader reaches two-thirds of the frame before hard problems, and followers wait until that caption disappears', () => {
  assert.equal(fishPose(FILM_CUES.hardProblems.start, 0).x, 1600 * 2 / 3);
  assert.ok(FILM_CUES.join > FILM_CUES.hardProblems.end + .027 + .26);
  for (let time = 0; time <= FILM_CUES.join; time += .05) {
    for (let index = 1; index < 5; index++) assert.equal(fishPose(time, index).opacity, 0);
  }
});

test('the five fish swim together before teamwork and clear the frame before the jump', () => {
  for (let index = 0; index < 5; index++) {
    const joined = fishPose(FILM_CUES.teamwork.start, index);
    assert.equal(joined.opacity, 1);
    assert.ok(joined.x > 0 && joined.x < 1600);
    assert.ok(joined.y > 0 && joined.y < 900);
    assert.ok(joined.x > fishPose(FILM_CUES.cruise, index).x);
    const departed = fishPose(FILM_CUES.jump.start, index);
    assert.ok(departed.x - 300 * departed.scale > 1600);
    for (let time = 0; time <= FILM_DURATION; time += .1) {
      assert.ok(Object.values(fishPose(time, index)).every(Number.isFinite));
    }
  }
});

test('the jump wipes to white before the question, then the actual arms enter for We connect', () => {
  assert.ok(FILM_CUES.jump.start > FILM_CUES.clear);
  assert.equal(finalePose(FILM_CUES.jump.start).white, 0);
  const airborne = finalePose((FILM_CUES.jump.start + FILM_CUES.jump.end) / 2);
  assert.ok(airborne.jumpAnimationTime > 0 && airborne.jumpAnimationTime < 1140);
  assert.equal(airborne.jumpOpacity, 1);
  const question = finalePose(FILM_CUES.question.start);
  assert.equal(question.white, 1);
  assert.equal(question.jumpOpacity, 0);
  assert.equal(question.hands, 0);
  assert.equal(textPose(FILM_CUES.connect.start, FILM_CUES.question.start, FILM_CUES.question.end + 4 * .027).opacity, 0);
  assert.equal(finalePose(FILM_DURATION).hands, 1);
  assert.equal(finalePose(FILM_DURATION).white, 1);
  assert.equal(textPose(FILM_CUES.hands.end, FILM_CUES.connect.start, FILM_CUES.connect.end).opacity, 1);
  assert.ok(FILM_CUES.hands.end - FILM_CUES.hands.start >= 1.7, 'the arms should take a slow, visible approach');
  assert.equal(finalePose((FILM_CUES.hands.start + FILM_CUES.hands.end) / 2).hands, .5);
});

test('the two-second lead-in is empty before the first words and dots', () => {
  for (let elapsed = 0; elapsed < FILM_LEAD_IN; elapsed += .1) {
    const time = filmContentTime(elapsed);
    assert.equal(textPose(time, .08, FILM_CUES.openingOut).opacity, 0);
    FISH_NODES.forEach((_, index) => assert.equal(nodePose(time, index).opacity, 0));
    assert.equal(finalePose(time).jumpOpacity, 0);
    assert.equal(finalePose(time).hands, 0);
    assert.equal(brandClosingPose(time).wordmarkOpacity, 0);
  }
});

test('the arms visibly lift, turn and open their fingers, then settle into the original pose', () => {
  const middle = handApproachPose((FILM_CUES.hands.start + FILM_CUES.hands.end) / 2);
  assert.ok(middle.lift > .1 && middle.turn > .08 && middle.curl > .08);
  assert.deepEqual(handApproachPose(FILM_CUES.hands.end), { entrance: 1, travel: 0, lift: 0, turn: 0, curl: 0 });
});

test('two dots connect, then the narration interval is visually blank for ten seconds', () => {
  const connected = brandClosingPose(BRAND_CUES.dots.end - .2);
  assert.equal(connected.pairOpacity, 1);
  assert.equal(connected.firstDot, 1);
  assert.equal(connected.secondDot, 1);
  assert.equal(connected.connection, 1);
  for (let time = BRAND_CUES.blank.start; time < BRAND_CUES.blank.end; time += .1) {
    const pose = brandClosingPose(time);
    for (const key of ['pairOpacity', 'jumpOpacity', 'fishOpacity', 'wordmarkOpacity', 'splashOpacity', 'periodOpacity', 'dropletsOpacity', 'impactOpacity'] as const) assert.equal(pose[key], 0);
    assert.equal(finalePose(time).handsOpacity, 0);
    assert.equal(textPose(time, FILM_CUES.connect.start, FILM_CUES.connect.end).opacity, 0);
  }
});

test('the second jump lands beside the wordmark and its splash becomes the period', () => {
  const airborne = brandClosingPose((BRAND_CUES.return.start + BRAND_CUES.return.end) / 2);
  assert.ok(airborne.fishArc > .9);
  assert.equal(BRAND_CUES.splash.start, BRAND_CUES.return.end);
  for (let time = BRAND_CUES.jump.start; time <= BRAND_CUES.return.end; time += .005) {
    const pose = brandClosingPose(time);
    assert.equal(pose.splashOpacity, 0, 'the splash must wait for the landing');
    assert.equal(pose.dropletsOpacity, 0);
    assert.equal(pose.impactOpacity, 0);
    assert.equal(pose.periodOpacity, 0);
  }
  const impact = brandClosingPose(BRAND_CUES.return.end + .05);
  assert.equal(impact.splashOpacity, 1);
  assert.ok(impact.dropletsOpacity > .8 && impact.impactOpacity > .5);
  assert.equal(airborne.periodOpacity, 0);
  const end = brandClosingPose(filmContentTime(FILM_DURATION));
  assert.equal(end.fishTravel, 1);
  assert.equal(end.fishArc, 0);
  assert.equal(end.fishRotation, 0);
  assert.equal(end.fishSettle, 0);
  assert.equal(end.fishOpacity, 1);
  assert.equal(end.wordmarkOpacity, 1);
  assert.equal(end.splashOpacity, 0);
  assert.equal(end.periodOpacity, 1);
  assert.equal(end.impactOpacity, 0);
  assert.equal(end.dropletsOpacity, 0);
  assert.ok(filmContentTime(FILM_DURATION) - BRAND_CUES.splash.end >= .6, 'hold the completed logo');
});

test('text fades in from the right and fades out to the left', () => {
  const entering = textPose(.1, 0, 1);
  assert.ok(entering.x > 0 && entering.opacity > 0 && entering.opacity < 1);
  assert.deepEqual(textPose(.5, 0, 1), { x: 0, opacity: 1 });
  const leaving = textPose(1.1, 0, 1);
  assert.ok(leaving.x < 0 && leaving.opacity > 0 && leaving.opacity < 1);
  assert.equal(textPose(1.3, 0, 1).opacity, 0);
});

const brandLayouts: BrandLayout[] = [
  { fishWidth: 153.6, fishX: 424, fishY: 360, wordmarkLeft: 533, wordmarkTop: 302, periodX: 930, periodY: 392, periodSize: 7.5 },
  { fishWidth: 61.4, fishX: 69, fishY: 422, wordmarkLeft: 114, wordmarkTop: 400, periodX: 300, periodY: 435, periodSize: 3.4 },
  { fishWidth: 100, fishX: 90, fishY: 300, wordmarkLeft: 145, wordmarkTop: 275, periodX: 380, periodY: 318, periodSize: 5 },
];

test('both fish jumps stay left of every letter, including their rotated bounds', () => {
  for (const layout of brandLayouts) {
    const initial = brandFishPosition(BRAND_CUES.return.start, layout);
    for (let time = BRAND_CUES.jump.start; time <= BRAND_CUES.return.end; time += .01) {
      const fish = brandFishPosition(time, layout);
      assert.equal(fish.x, initial.x, 'the fish must stay in its own space');
      const angle = fish.rotation * Math.PI / 180;
      const halfWidth = Math.abs(Math.cos(angle)) * layout.fishWidth / 2 + Math.abs(Math.sin(angle)) * layout.fishWidth / 4;
      assert.ok(fish.x + halfWidth <= layout.wordmarkLeft - 8);
      const firstJumpRadius = Math.hypot(19, 8) * .84 * layout.fishWidth * 2.08 / 72;
      assert.ok(fish.x + firstJumpRadius < layout.wordmarkLeft - 8);
    }
    const landed = brandFishPosition(BRAND_CUES.return.end, layout);
    assert.equal(landed.y, layout.fishY);
    const before = brandFishPosition(BRAND_CUES.return.end - .001, layout);
    const after = brandFishPosition(BRAND_CUES.return.end + .001, layout);
    assert.ok(before.y < landed.y && after.y > landed.y, 'the fish carries its downward momentum through contact');
    assert.ok(Math.abs((landed.y - before.y) - (after.y - landed.y)) < .01, 'the landing should not snap between poses');
    const settled = brandFishPosition(BRAND_CUES.settle.end, layout);
    assert.equal(settled.y, layout.fishY);
    assert.ok(Math.abs(brandFishPosition(BRAND_CUES.settle.end - .001, layout).y - settled.y) < .001);
  }
});

test('the splash rises beside the fish and stays clear of the letters on its way to the period', () => {
  for (const layout of brandLayouts) {
    const radius = layout.periodSize / 2;
    const lettersRight = layout.periodX - radius - 2;
    const fish = brandFishPosition(BRAND_CUES.return.end, layout);
    assert.deepEqual(brandSplashPosition(BRAND_CUES.splash.start, layout), { x: fish.contactX, y: fish.surfaceY });
    for (let time = BRAND_CUES.splash.start; time < BRAND_CUES.splash.end; time += .005) {
      const dot = brandSplashPosition(time, layout);
      assert.ok(dot.x + radius < layout.wordmarkLeft || dot.y + radius < layout.wordmarkTop || dot.x - radius > lettersRight);
    }
    const landed = brandSplashPosition(BRAND_CUES.splash.end, layout);
    assert.equal(landed.x, layout.periodX);
    assert.ok(Math.abs(landed.y - layout.periodY) < .001);
  }
});
