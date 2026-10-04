'use client';

import { communityHref } from '@/lib/community-recommendations';
import { communityStyle } from '@/lib/community-colors';
import type { CommunityRecommendation } from '@/lib/types';

export default function CommunityRecommendations({ communities, onCommunity }: {
  communities: CommunityRecommendation[];
  onCommunity?: (id: string) => void;
}) {
  if (!communities.length) return null;
  return <section className="answer-communities" aria-label="Recommended communities">
    {communities.map(community => <a key={community.id} className="evidence-card answer-community" style={communityStyle(community.id)} aria-label={`Explore community: ${community.name}`} href={communityHref(community.id)} onClick={event => {
      if (onCommunity && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); onCommunity(community.id); }
    }}>
      <span className="answer-community-copy"><strong title={community.name}>{community.name}</strong><span>{community.memberCount} {community.memberCount === 1 ? 'member' : 'members'} · {community.postCount} {community.postCount === 1 ? 'post' : 'posts'}</span></span>
    </a>)}
  </section>;
}
