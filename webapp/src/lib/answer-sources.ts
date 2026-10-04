import type { Source } from './types';

export function remapCitations(text: string, requestedIds: string[], selected: Source[]) {
  return text.replace(/\[(\d+(?:\s*,\s*\d+)*)\](?!\()/g, (_, values: string) => {
    const numbers = values.split(',').map(value => selected.findIndex(s => s.id === requestedIds[Number(value.trim()) - 1]) + 1).filter(Boolean);
    return numbers.length ? `[${[...new Set(numbers)].join(', ')}]` : '';
  });
}

// Place verified cards after the paragraph/list/table that cites them. Fenced
// code stays intact; uncited retrieved records remain visible at the end.
export function answerWithSources(text: string, sources: Source[]) {
  const blocks: string[] = [];
  let lines: string[] = [], fence: string | undefined;
  for (const line of text.split('\n')) {
    const marker = line.match(/^\s*(`{3,}|~{3,})/)?.[1];
    if (marker && !fence) fence = marker[0];
    else if (marker?.[0] === fence) fence = undefined;
    if (!line.trim() && !fence && lines.length) { blocks.push(lines.join('\n')); lines = []; }
    else lines.push(line);
  }
  if (lines.length) blocks.push(lines.join('\n'));
  const used = new Set<string>();
  const parts = blocks.map(block => {
    const cards: { source: Source; number: number }[] = [];
    if (!/^\s*(`{3,}|~{3,})/.test(block)) {
      for (const match of block.matchAll(/\[(\d+(?:\s*,\s*\d+)*)\](?!\()/g)) {
        for (const value of match[1].split(',')) {
          const number = Number(value.trim()), source = sources[number - 1];
          if (source && !used.has(source.id)) { used.add(source.id); cards.push({ source, number }); }
        }
      }
    }
    return { text: block, cards };
  });
  const remaining = sources.flatMap((source, index) => {
    if (used.has(source.id)) return [];
    used.add(source.id);
    return [{ source, number: index + 1 }];
  });
  if (remaining.length) parts.push({ text: '', cards: remaining });
  return parts;
}
