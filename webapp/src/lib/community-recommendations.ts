import type { CommunityRecommendation, ConditionOption } from './types';

/** Punctuation and spacing variants share a community; subtype words and numbers remain significant. */
export function normalizeCondition(value: string): string {
  return value.normalize('NFKC').toLocaleLowerCase('en').replace(/[’'`]/g, '').replace(/\+/g, ' plus ').replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');
}

export function communityHref(id: string): string {
  return `/?view=community&condition=${encodeURIComponent(id)}`;
}

// Match named conditions, not symptoms or incidental substrings (e.g. SMA in
// "plasma"). Only recommend real entries from the community directory.
export function recommendCommunities(query: string, conditions: ConditionOption[]): CommunityRecommendation[] {
  const text = ` ${normalizeCondition(query)} `;
  const matches: { condition: ConditionOption; start: number; end: number }[] = [];
  for (const condition of conditions) {
    for (const alias of new Set([condition.name, ...(condition.aliases || [])].map(normalizeCondition))) {
      if (!alias) continue;
      const phrase = ` ${alias} `;
      let start = text.indexOf(phrase);
      while (start !== -1) {
        matches.push({ condition, start, end: start + phrase.length });
        start = text.indexOf(phrase, start + 1);
      }
    }
  }
  // Prefer a specific subtype over a shorter parent name at the same position.
  const selected = matches.filter(match => !matches.some(other => other.condition.id !== match.condition.id && other.start <= match.start && other.end >= match.end && other.end - other.start > match.end - match.start));
  selected.sort((a, b) => a.start - b.start || b.end - a.end);
  return [...new Map(selected.map(({ condition }) => [condition.id, condition])).values()].slice(0, 3)
    .map(({ id, name, memberCount, postCount }) => ({ id, name, memberCount, postCount }));
}
