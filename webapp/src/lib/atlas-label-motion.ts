import type { PlacedLabel } from './atlas-labels';

export type AnimatedAtlasLabel = PlacedLabel & { opacity: number; interactive: boolean };
type LabelState = { label: AnimatedAtlasLabel; appearedAt: number; missingAt?: number };

const FADE_MS = 220;
const ENTER_DELAY_MS = 80;
const EXIT_DELAY_MS = 140;

/** Retain keyed labels through brief layout gaps, including their exit frames. */
export class AtlasLabelMotion {
  private states = new Map<string, LabelState>();
  private lastTime: number | undefined;

  update(targets: PlacedLabel[], now: number, reducedMotion = false) {
    // A resumed background tab should still fade, not jump to the endpoint.
    const elapsed = this.lastTime === undefined ? 0 : Math.max(0, Math.min(64, now - this.lastTime));
    this.lastTime = now;
    const byId = new Map(targets.map(label => [label.id, label]));
    for (const target of targets) {
      if (!this.states.has(target.id)) this.states.set(target.id, {
        label: { ...target, opacity: 0, interactive: false }, appearedAt: now,
      });
    }

    const labels: AnimatedAtlasLabel[] = [];
    let animating = false;
    for (const [id, state] of this.states) {
      const target = byId.get(id);
      const old = state.label;
      if (target) state.missingAt = undefined;
      else state.missingAt ??= now;
      // Large moves change alignment and can cross other tags. Fade to the
      // new slot; only nearby adjustments travel across the screen.
      const relocating = !!target && (target.side !== old.side || Math.hypot(target.x - old.x, target.y - old.y) > 160);
      const entering = now - state.appearedAt < ENTER_DELAY_MS;
      const retained = !target && now - state.missingAt! < EXIT_DELAY_MS;
      const desiredOpacity = relocating ? 0 : target ? (entering ? 0 : 1) : retained ? old.opacity : 0;
      const opacity = Math.max(0, Math.min(1, old.opacity + Math.sign(desiredOpacity - old.opacity) * Math.min(Math.abs(desiredOpacity - old.opacity), elapsed / FADE_MS)));
      let next = { ...old, opacity, interactive: !!target && !relocating && opacity > .5 };

      if (target) {
        if (relocating) {
          if (opacity === 0) next = { ...target, opacity: 0, interactive: false };
        } else {
          const amount = reducedMotion ? 1 : 1 - Math.exp(-elapsed / 85);
          const approach = (from: number, to: number) => Math.abs(to - from) < .25 ? to : from + (to - from) * amount;
          next = { ...target, opacity, interactive: next.interactive,
            x: approach(old.x, target.x), y: approach(old.y, target.y),
            width: approach(old.width, target.width), height: approach(old.height, target.height),
          };
        }
        animating ||= entering || relocating || opacity !== 1 || next.x !== target.x || next.y !== target.y || next.width !== target.width || next.height !== target.height;
      } else if (opacity === 0) {
        this.states.delete(id);
        continue;
      } else {
        animating = true;
      }

      state.label = next;
      labels.push(next);
    }
    return { labels, animating };
  }
}
