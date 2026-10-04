'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowUpRight, LoaderCircle, Search, X } from 'lucide-react';
import { atlasRegions } from '@/lib/atlas-graph';
import { diseaseGraphHref, organDiseaseListSchema, type OrganDisease, type OrganDiseaseList } from '@/lib/organ-diseases';
import styles from './OrganDiseases.module.css';

// Exported from the HPO annotations by scripts/export_organ_diseases.py.
const cache = new Map<string, Promise<OrganDiseaseList>>();
function loadOrgan(regionId: string) {
  if (!cache.has(regionId)) {
    const request = fetch(`/organ-diseases/${encodeURIComponent(regionId)}.json`).then(async response => {
      if (!response.ok) throw new Error('The disease list for this organ could not be loaded.');
      return organDiseaseListSchema.parse(await response.json());
    });
    request.catch(() => cache.delete(regionId));
    cache.set(regionId, request);
  }
  return cache.get(regionId)!;
}

const PAGE = 60;
const normalize = (text: string) => text.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, ' ').trim();
const sorts = { involvement: 'Organ involvement', name: 'Name (A–Z)', findings: 'Number of findings' } as const;

export default function OrganDiseases({ regionId }: { regionId: string }) {
  const router = useRouter();
  const region = atlasRegions[regionId];
  const [data, setData] = useState<OrganDiseaseList>();
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<keyof typeof sorts>('involvement');
  const [shown, setShown] = useState(PAGE);
  const [opening, setOpening] = useState('');

  useEffect(() => {
    let live = true;
    setData(undefined); setError(''); setQuery(''); setShown(PAGE); setOpening('');
    loadOrgan(regionId).then(next => { if (live) setData(next); })
      .catch(e => { if (live) setError(e instanceof Error ? e.message : 'The disease list could not be loaded.'); });
    return () => { live = false; };
  }, [regionId, retry]);

  const matches = useMemo(() => {
    if (!data) return [];
    const words = normalize(query).split(' ').filter(Boolean);
    const found = words.length ? data.diseases.filter(d => {
      const text = normalize([d.name, d.id, ...(d.ids ?? []), ...d.genes, ...d.top].join(' '));
      return words.every(word => text.includes(word));
    }) : data.diseases;
    if (sort === 'name') return [...found].sort((a, b) => a.name.localeCompare(b.name));
    if (sort === 'findings') return [...found].sort((a, b) => b.features - a.features || b.score - a.score);
    return found;
  }, [data, query, sort]);

  function open(disease: OrganDisease) {
    setOpening(disease.id);
    router.push(diseaseGraphHref(disease));
  }

  return <aside className={styles.panel} aria-label={`Diseases involving ${region?.label ?? regionId}`}>
    <header className={styles.header}>
      <span className={styles.eyebrow}>Diseases involving</span>
      <h2>{region?.label ?? data?.label ?? regionId}</h2>
      <p>{data ? <>{data.total.toLocaleString()} diseases with a recorded finding under <i>{data.hpo_label}</i> ({data.hpo}){data.scope ? `. ${data.scope}.` : '.'}</> : error ? '' : 'Loading the disease list…'}</p>
    </header>
    <div className={styles.tools}>
      <label className={styles.search}><Search size={13} aria-hidden="true"/>
        <input value={query} onChange={e => { setQuery(e.target.value); setShown(PAGE); }} placeholder="Filter by disease, gene or finding" aria-label={`Filter diseases involving ${region?.label ?? regionId}`} maxLength={120} disabled={!data}/>
        {query && <button onClick={() => setQuery('')} aria-label="Clear filter"><X size={12}/></button>}
      </label>
      <select value={sort} onChange={e => { setSort(e.target.value as keyof typeof sorts); setShown(PAGE); }} aria-label="Sort diseases" disabled={!data}>
        {Object.entries(sorts).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </div>
    {error && <div className={styles.status} role="alert"><p>{error}</p><button onClick={() => setRetry(v => v + 1)}>Retry</button></div>}
    {!data && !error && <div className={styles.status} role="status"><LoaderCircle size={16} className={styles.spin}/>Loading diseases…</div>}
    {data && <>
      <p className={styles.count} role="status">{query ? `${matches.length.toLocaleString()} of ${data.total.toLocaleString()} diseases match` : `Ranked by how many findings in this organ the disease is expected to show`}</p>
      <ol className={styles.list}>
        {matches.slice(0, shown).map(d => <li key={d.id}>
          <button className={styles.disease} onClick={() => open(d)} disabled={!!opening} aria-label={`Open the knowledge graph of ${d.name}`}>
            <span className={styles.name}>{d.name}{opening === d.id ? <LoaderCircle size={13} className={styles.spin}/> : <ArrowUpRight size={13} aria-hidden="true"/>}</span>
            <span className={styles.meta}>
              <span>{[d.id, ...(d.ids ?? []).filter(id => id !== d.id)].join(' · ')}</span>
              <span className={styles.chip} data-frequency={d.frequency}>{d.frequency === 'unknown' ? 'frequency unknown' : d.frequency}</span>
              <span>{d.features} {d.features === 1 ? 'finding' : 'findings'}</span>
              {d.genes.length > 0 && <span className={styles.genes}>{d.genes.join(', ')}</span>}
            </span>
            <span className={styles.findings}>{d.top.join(' · ')}</span>
          </button>
        </li>)}
      </ol>
      {!matches.length && <p className={styles.status}>No diseases in this list match “{query}”.</p>}
      {matches.length > shown && <button className={styles.more} onClick={() => setShown(n => n + PAGE)}>Show more ({(matches.length - shown).toLocaleString()} left)</button>}
      <p className={styles.provenance}>{data.source}, exported {data.exported}. A listing means the disease has an annotated finding in this organ, not that every patient has it.</p>
    </>}
  </aside>;
}
