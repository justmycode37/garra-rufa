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
  const maxLabelWidth = Math.min(240, (width - margin * 2) * .35);
  const radius = Math.min(width * .28, Math.max(width * .17, (maxX - minX) / 2 + 90));
  const rows = Math.ceil(records.length / 2);
  const gap = Math.min(96, (bottom - top) / Math.max(rows, 1));
  const midY = Math.max(top + rows * gap / 2, Math.min(bottom - rows * gap / 2, center.y));
  const placements = records.map((record, index) => {
    const side = index % 2 ? 'right' as const : 'left' as const;
    const row = Math.floor(index / 2);
    const phase = rows > 1 ? row / (rows - 1) : .5;
    // A staggered orbit: different widths and distances avoid rigid columns.
    const reach = radius * (.62 + .42 * Math.sin(phase * Math.PI) + (side === 'right' ? .1 : -.06));
    const labelWidth = Math.min(maxLabelWidth, Math.max(150, record.label.length * 6.2 + 44));
    const x = Math.max(margin, Math.min(width - margin - labelWidth, center.x + (side === 'left' ? -reach - labelWidth : reach)));
    const labelHeight = Math.min(bottom - top, Math.max(56, Math.min(3, Math.ceil(record.label.length * (width < 600 ? 5.5 : 6.5) / Math.max(50, labelWidth - 44))) * 18 + 24));
    const stagger = (side === 'right' ? .19 : -.12) * gap;
    const y = Math.max(top, Math.min(bottom - labelHeight, midY + (row - (rows - 1) / 2) * gap + stagger - labelHeight / 2));
    return { record, side, x, y, width: labelWidth, height: labelHeight };
  });
  // Keep the floating arrangement readable when space is tight.
  for (const side of ['left', 'right'] as const) {
    const column = placements.filter(item => item.side === side);
    let edge = top;
    for (const item of column) { item.y = Math.max(item.y, edge); edge = item.y + item.height + 8; }
    edge = bottom;
    for (const item of column.reverse()) { item.y = Math.min(item.y, edge - item.height); edge = item.y - 8; }
  }
  const used = new Set<string>();
  return placements.map(({ record, side, x, y, width: labelWidth, height: labelHeight }) => {
    // Connect just inside the widget so the line meets its edge.
    const end = { x: side === 'left' ? x + labelWidth - 8 : x + 8, y: y + labelHeight / 2 };
    const prior = previous.find(item => item.id === record.id);
    const priorAnchor = prior && visible.find(point => point.id === prior.anchor.id && !used.has(point.id));
    const distance = (point: AnatomicalAnchor) => Math.hypot(point.x - end.x, point.y - end.y) + Math.abs(point.y - end.y) * .8;
    const anchor = priorAnchor ?? visible.filter(point => !used.has(point.id)).sort((a, b) => distance(a) - distance(b))[0];
    if (!anchor) return undefined;
    used.add(anchor.id);
    return { id: record.id, x, y, width: labelWidth, height: labelHeight, side, anchor, end };
  }).filter((item): item is AtlasCallout => !!item);
}
