export type ScreenPoint = { x: number; y: number };
export type LabelCandidate = { id: string; title: string; anchor: ScreenPoint; kind: 'region' | 'organ' | 'disease'; target?: string; diseaseId?: string };
export type PlacedLabel = LabelCandidate & { x: number; y: number; width: number; height: number; side: 'left' | 'right'; overlapsGraph: boolean };
export type SilhouetteRow = { left: number; right: number };
export const SILHOUETTE_STEP = 10;

/** Prefer free space; disease titles always get an on-screen fallback. */
export function placeAtlasLabels(candidates: LabelCandidate[], outline: SilhouetteRow[], width: number, height: number, previous: PlacedLabel[] = []): PlacedLabel[] {
  const margin = width < 600 ? 18 : 38;
  const maxLabelWidth = Math.min(width < 600 ? 145 : 236, width * .34);
  const placed: PlacedLabel[] = [];
  const top = 74, bottom = height - 88;
  const previousById = new Map(previous.map((label, index) => [label.id, { label, index }]));
  for (const candidate of [...candidates].sort((a, b) => Number(b.kind === 'disease') - Number(a.kind === 'disease') || (previousById.get(a.id)?.index ?? candidates.length) - (previousById.get(b.id)?.index ?? candidates.length))) {
    const prior = previousById.get(candidate.id)?.label;
    // Keep a valid slot until another is meaningfully better. Small camera
    // movements should not swap labels between rows or opposite margins.
    const movementCost = (option: { x: number; y: number; side: 'left' | 'right' }) => prior ? Math.hypot(option.x - prior.x, option.y - prior.y) * .45 + (option.side !== prior.side ? 80 : 0) : 0;
    if (candidate.kind !== 'disease' && (candidate.anchor.x < -20 || candidate.anchor.x > width + 20 || candidate.anchor.y < -20 || candidate.anchor.y > height + 20)) continue;
    const font = width < 600 ? 14 : 16;
    const labelWidth = width < 600 ? Math.min(maxLabelWidth, candidate.title.length * font * .6 + 24) : maxLabelWidth;
    const lines = Math.max(1, Math.ceil(candidate.title.length * font * .57 / labelWidth));
    const labelHeight = lines * font * 1.5 + 14;
    const options: { x: number; y: number; side: 'left' | 'right'; score: number }[] = [];
    for (const side of ['left', 'right'] as const) {
      const x = side === 'left' ? margin : width - margin - labelWidth;
      for (let y = top; y + labelHeight <= bottom; y += 16) {
        // First try short leader lines in genuinely empty space.
        if (Math.abs(candidate.anchor.y - y - labelHeight / 2) > Math.max(110, height * .24)) continue;
        let clear = true;
        for (let row = Math.floor((y - 12) / SILHOUETTE_STEP); row <= Math.ceil((y + labelHeight + 12) / SILHOUETTE_STEP); row++) {
          const span = outline[row];
          if (span && x < span.right + 18 && x + labelWidth > span.left - 18) { clear = false; break; }
        }
        if (!clear || placed.some(other => x < other.x + other.width + 12 && x + labelWidth > other.x - 12 && y < other.y + other.height + 12 && y + labelHeight > other.y - 12)) continue;
        const endX = side === 'left' ? x + labelWidth : x;
        const wrongSide = side === 'left' ? candidate.anchor.x < endX : candidate.anchor.x > endX;
        if (wrongSide) continue;
        const distance = Math.hypot(candidate.anchor.x - endX, candidate.anchor.y - y - labelHeight / 2);
        const score = distance + Math.abs(candidate.anchor.y - y - labelHeight / 2) * .7;
        if (score < Math.max(width * .75, 260)) options.push({ x, y, side, score });
      }
    }
    options.sort((a, b) => a.score + movementCost(a) - b.score - movementCost(b));
    let best = options[0];
    if ((!best || best.score >= Math.max(width * .75, 260)) && candidate.kind === 'disease') {
      const aim = { x: Math.max(margin, Math.min(width - margin, candidate.anchor.x)), y: Math.max(top, Math.min(bottom, candidate.anchor.y)) };
      const columns = [margin, width - margin - labelWidth, Math.max(margin, Math.min(width - margin - labelWidth, aim.x - labelWidth / 2))];
      const fallback: typeof options = [];
      for (const x of columns) {
        for (let y = top; y + labelHeight <= bottom; y += 12) {
          const overlap = placed.reduce((area, other) => area + Math.max(0, Math.min(x + labelWidth + 10, other.x + other.width) - Math.max(x - 10, other.x)) * Math.max(0, Math.min(y + labelHeight + 10, other.y + other.height) - Math.max(y - 10, other.y)), 0);
          const side = aim.x >= x + labelWidth / 2 ? 'left' : 'right';
          const endX = side === 'left' ? x + labelWidth : x;
          const coversAnchor = aim.x > x && aim.x < x + labelWidth && aim.y > y && aim.y < y + labelHeight;
          fallback.push({ x, y, side, score: overlap * 1000 + Math.hypot(aim.x - endX, aim.y - y - labelHeight / 2) + (coversAnchor ? 200 : 0) });
        }
      }
      fallback.sort((a, b) => a.score + movementCost(a) - b.score - movementCost(b));
      best = fallback[0];
    }
    if (best && (candidate.kind === 'disease' || best.score < Math.max(width * .75, 260))) {
      let overlapsGraph = false;
      for (let row = Math.floor(best.y / SILHOUETTE_STEP); row <= Math.ceil((best.y + labelHeight) / SILHOUETTE_STEP); row++) {
        const span = outline[row];
        if (span && best.x < span.right && best.x + labelWidth > span.left) { overlapsGraph = true; break; }
      }
      placed.push({ ...candidate, ...best, width: labelWidth, height: labelHeight, overlapsGraph });
    }
  }
  return placed;
}
