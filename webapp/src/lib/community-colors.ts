import type { CSSProperties } from 'react';

const palette = ['sea', 'lilac', 'coral', 'olive', 'lavender', 'sky', 'peach', 'butter'] as const;
const established: Record<string, typeof palette[number]> = {
  cmt: 'sea', eds: 'lilac', fabry: 'coral', marfan: 'olive',
  huntington: 'lavender', sma: 'sky', pompe: 'peach', rett: 'butter',
};

// Community IDs are stable across names, navigation and saved recommendations.
export function communityColor(id: string) {
  let hash = 0;
  for (const character of id) hash = (Math.imul(hash, 31) + character.charCodeAt(0)) >>> 0;
  return established[id] || palette[hash % palette.length];
}

export function communityStyle(id: string): CSSProperties {
  return { '--community-color': `color-mix(in srgb, var(--${communityColor(id)}) 45%, var(--page))` } as CSSProperties;
}
