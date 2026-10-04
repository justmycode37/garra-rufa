export type AnatomicalAnchor = { id: string; x: number; y: number };
export type AtlasCallout = { id: string; x: number; y: number; width: number; height: number; side: 'left' | 'right'; anchor: AnatomicalAnchor; end: { x: number; y: number } };

/** Place individual record labels around the selected anatomy, not a screen sidebar. */
export function placeAtlasCallouts(records: { id: string; label: string }[], anchors: AnatomicalAnchor[], viewport: { width: number; height: number }, focus?: { x: number; y: number }, previous: AtlasCallout[] = []): AtlasCallout[] {
  const { width, height } = viewport;
  const top = 138, bottom = height - 210, margin = 18;
  const visible = anchors.filter(p => p.x >= 0 && p.x <= width && p.y >= top && p.y <= bottom);
  if (!visible.length || bottom - top < 60) return [];
  const minX = Math.min(...visible.map(p => p.x)), maxX = Math.max(...visible.map(p => p.x));
  const minY = Math.min(...visible.map(p => p.y)), maxY = Math.max(...visible.map(p => p.y));
  const center = focus && focus.x >= 0 && focus.x <= width && focus.y >= top && focus.y <= bottom ? focus : { x: (minX + maxX) / 2, y: (minY + maxY) / 2 };
  const labelWidth = Math.min(220, (width - margin * 2) * .29);
  const radius = Math.min(width * .16, Math.max(48, (maxX - minX) / 2 + 30));
  const rows = Math.ceil(records.length / 2);
  const gap = Math.min(96, (bottom - top) / Math.max(rows, 1));
  const midY = Math.max(top + rows * gap / 2, Math.min(bottom - rows * gap / 2, center.y));
  const used = new Set<string>();
  return records.map((record, index) => {
    const side = index % 2 ? 'right' : 'left';
    const x = Math.max(margin, Math.min(width - margin - labelWidth, center.x + (side === 'left' ? -radius - labelWidth : radius)));
    const labelHeight = Math.min(3, Math.ceil(record.label.length * 7 / Math.max(50, labelWidth - 8))) * 18 + 10;
    const y = midY + (Math.floor(index / 2) - (rows - 1) / 2) * gap - labelHeight / 2;
    const end = { x: side === 'left' ? x + labelWidth : x, y: y + labelHeight / 2 };
    const prior = previous.find(item => item.id === record.id);
    const priorAnchor = prior && visible.find(point => point.id === prior.anchor.id && !used.has(point.id));
    const distance = (point: AnatomicalAnchor) => Math.hypot(point.x - end.x, point.y - end.y) + Math.abs(point.y - end.y) * .8;
    const anchor = priorAnchor ?? visible.filter(point => !used.has(point.id)).sort((a, b) => distance(a) - distance(b))[0];
    if (!anchor) return undefined;
    used.add(anchor.id);
    return { id: record.id, x, y, width: labelWidth, height: labelHeight, side, anchor, end };
  }).filter((item): item is AtlasCallout => !!item);
}
