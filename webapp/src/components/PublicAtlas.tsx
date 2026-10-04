'use client';

import dynamic from 'next/dynamic';
import type { Disease } from '@/lib/types';

const DiseaseAtlas = dynamic(() => import('./DiseaseAtlas'), { ssr: false });

/** Public research discovery shares the workspace atlas without private context. */
export default function PublicAtlas({onDisease, onCommunity}: {
  onDisease: (disease: Disease) => void;
  onCommunity: (id: string) => void;
}) {
  return <DiseaseAtlas composerContext={{}} onDisease={onDisease} onCommunity={onCommunity}
    onAskWithContext={async (query, _history, _onText, signal) => {
      const response = await fetch('/api/discovery?resource=search', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({query, mode: 'text', limit: 8}), signal,
      });
      const data = await response.json();
      if (!response.ok) throw Error(data.error || 'Search unavailable');
      return {id: crypto.randomUUID(), role: 'assistant', text: data.results.length
        ? `Found ${data.total} records. Select an entity in Connected research to inspect its groups and evidence.\n\n${data.results.map((r: {name: string; id: string}) => `${r.name} (${r.id})`).join('\n')}`
        : 'No matching records in the loaded snapshot. Try a disease name, gene or identifier.'};
    }}/>
}
