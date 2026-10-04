'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowUpRight, ChevronLeft, ChevronRight, LoaderCircle, Search, X } from 'lucide-react';
import { atlasGraphSchema, atlasNodeKind, atlasPaths, atlasRegions, mergeAtlasGraphs, repositoryGraphSchema, type AtlasGraphData, type AtlasNode } from '@/lib/atlas-graph';
import type { AnatomyFrame } from '@/lib/anatomy-scene';
import { placeAtlasCallouts, type AtlasCallout } from '@/lib/atlas-callouts';
import styles from './AtlasKnowledgeGraph.module.css';

const cache = new Map<string, { at: number; data: AtlasGraphData }>();
const filters = [['all', 'All'], ['disease', 'Diseases'], ['phenotype', 'Phenotypes'], ['gene', 'Genes'], ['specialist', 'Doctors & centres'], ['paper', 'Papers'], ['claim', 'Claims'], ['pathway', 'Pathways & processes'], ['trial', 'Trials'], ['resource', 'Resources']] as const;
const kindLabel = (node: AtlasNode) => node.kind.replaceAll('_', ' ');
const relationLabel = (relation: string) => relation.replaceAll('_', ' ');
const widgetTones = ['lilac', 'sea', 'butter', 'peach', 'sky'] as const;
function widgetTone(id: string) {
  // Keep a record's widget and connector the same color while navigating.
  const hash = Array.from(id).reduce((value, letter) => (value * 31 + letter.charCodeAt(0)) >>> 0, 0);
  return widgetTones[hash % widgetTones.length];
}

export default function AtlasKnowledgeGraph({ regionId, frame, onAsk }: { regionId: string; frame: AnatomyFrame; onAsk: (query: string) => void }) {
  const region = atlasRegions[regionId];
  const [data, setData] = useState<AtlasGraphData>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState('');
  const [filterQuery, setFilterQuery] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [filter, setFilter] = useState('all');
  const [page, setPage] = useState(0);
  const [expanding, setExpanding] = useState(false);
  const [expandNotice, setExpandNotice] = useState('');
  const [liveState, setLiveState] = useState<'not_requested'|'loading'|'loaded'|'partial'>('not_requested');
  const [expanded, setExpanded] = useState<string[]>([]);
  const expandRequest = useRef<AbortController | null>(null);
  const previousCallouts = useRef<AtlasCallout[]>([]);

  useEffect(() => { const timer = setTimeout(() => { setFilterQuery(query); setOffset(0); }, 300); return () => clearTimeout(timer); }, [query]);
  useEffect(() => {
    const abort = new AbortController();
    expandRequest.current?.abort(); setExpanding(false); setExpandNotice(''); setExpanded([]); setLiveState('not_requested');
    setLoading(true); setData(undefined); setError(''); setSelectedId(''); setPage(0); setFilter('all');
    const key = `${regionId}:${offset}:${filterQuery}`;
    const timer = setTimeout(async () => {
      try {
        const cached = cache.get(key);
        let next = cached && Date.now() - cached.at < 600000 && !retry ? cached.data : undefined;
        if (!next) {
          const response = await fetch(`/api/atlas?${new URLSearchParams({ region: regionId, offset: String(offset), q: filterQuery })}`, { signal: abort.signal });
          const body = await response.json();
          if (!response.ok) throw new Error(body.error || 'Regional records could not be loaded.');
          next = atlasGraphSchema.parse(body);
          if (cache.size >= 24) cache.delete(cache.keys().next().value!);
          cache.set(key, { at: Date.now(), data: next });
        }
        if (!abort.signal.aborted) setData(next);
      } catch (error) { if (!abort.signal.aborted) setError(error instanceof Error ? error.message : 'Regional records could not be loaded.'); }
      finally { if (!abort.signal.aborted) setLoading(false); }
    }, 300);
    return () => { clearTimeout(timer); abort.abort(); expandRequest.current?.abort(); };
  }, [regionId, offset, filterQuery, retry]);

  const selected = data?.nodes.find(node => node.id === selectedId);
  const rootId = selected?.id ?? data?.root ?? '';
  const paths = useMemo(() => data ? atlasPaths(data, rootId) : new Map(), [data, rootId]);
  const records = useMemo(() => {
    if (!data) return [];
    return data.nodes.filter(node => node.id !== rootId && node.id !== data.root && paths.has(node.id))
      .sort((a, b) => {
        // Start with diseases, then show the closest evidence when drilling in.
        const rank = (node: AtlasNode) => selected ? paths.get(node.id)!.length : atlasNodeKind(node) === 'disease' ? 0 : atlasNodeKind(node) === 'phenotype' ? 1 : 2;
        return rank(a) - rank(b) || a.label.localeCompare(b.label);
      });
  }, [data, paths, rootId, selected]);
  const matches = (node: AtlasNode, kind: string) => kind === 'all' || (kind === 'specialist' ? ['doctor', 'centre'].includes(atlasNodeKind(node)) : atlasNodeKind(node) === kind);
  const filtered = records.filter(node => matches(node, filter));
  const viewport = frame.viewport ?? { width: 1000, height: 700 };
  const pageSize = Math.max(2, Math.min(viewport.width < 600 ? 4 : 8, Math.floor((viewport.height - 348) / 84) * 2));
  const recordPageSize = selected ? pageSize - 1 : pageSize;
  const lastPage = Math.max(0, Math.ceil(filtered.length / recordPageSize) - 1);
  const currentPage = Math.min(page, lastPage);
  const visible = [...(selected ? [selected] : []), ...filtered.slice(currentPage * recordPageSize, (currentPage + 1) * recordPageSize)];
  const callouts = placeAtlasCallouts(visible, frame.regionNodes ?? [], viewport, frame.regionAnchor, previousCallouts.current);
  useEffect(() => { previousCallouts.current = callouts; }, [callouts]);

  function select(node: AtlasNode) {
    expandRequest.current?.abort(); setExpanding(false);
    setSelectedId(node.id); setFilter('all'); setPage(0); setExpandNotice('');
    if (node.kind === 'disease' && /^(OMIM|ORPHA|MONDO|DECIPHER):\d+$/.test(node.id)) void expand(node);
  }
  async function expand(node: AtlasNode, enrich = false) {
    if (!enrich && expanded.includes(node.id)) return;
    const abort = new AbortController(); expandRequest.current?.abort(); expandRequest.current = abort;
    setExpanding(true); setExpandNotice(''); if(enrich)setLiveState('loading');
    try {
      const response = await fetch(`/api/atlas/expand?${new URLSearchParams({ entity: node.id, label: node.label.slice(0, 200), ...(enrich ? {enrich:'1'} : {}) })}`, { signal: abort.signal });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Connected records could not be loaded.');
      const graph = repositoryGraphSchema.parse(body.graph);
      if (abort.signal.aborted) return;
      setData(current => current ? mergeAtlasGraphs(current, [graph]) : current);
      const unavailable: string[] = Array.isArray(body.unavailableProviders) ? body.unavailableProviders.filter((item: unknown) => typeof item === 'string') : [];
      if(enrich)setLiveState(unavailable.length?'partial':'loaded');
      if (!unavailable.length) setExpanded(previous => [...previous, node.id]);
      setExpandNotice(unavailable.length ? `Some sources are unavailable: ${unavailable.join(', ')}. Retrieved connections are shown; you can retry.` : 'Available records loaded. Literature search results are not verified claims. Only source-listed people appear as doctors.');
    } catch (error) { if (!abort.signal.aborted) { if(enrich)setLiveState('partial'); setExpandNotice(error instanceof Error ? error.message : 'Connected records could not be loaded.'); } }
    finally { if (!abort.signal.aborted) setExpanding(false); }
  }

  return <div className={styles.layer} aria-label={`${region.label} knowledge graph`}>
    <div className={styles.toolbar}>
      <div className={styles.heading}><strong>{region.label}</strong><span>{loading ? 'Loading regional records…' : data ? `${data.total.toLocaleString()} disease records` : 'Regional knowledge'}</span></div>
      <label className={styles.search}><Search size={13}/><input aria-label="Filter regional disease records" value={query} onChange={event => setQuery(event.target.value)} placeholder="Find a disease in this region" maxLength={120}/>{query && <button aria-label="Clear disease filter" onClick={() => setQuery('')}><X size={12}/></button>}</label>
      {data && <select className={styles.filter} aria-label="Graph record type" value={filter} onChange={event => { setFilter(event.target.value); setPage(0); }}>{filters.map(([kind, label]) => {
        const count = records.filter(node => matches(node, kind)).length + (selected && selected.id !== data.root && matches(selected, kind) ? 1 : 0);
        const status = count > 0 ? String(count)
          : kind === 'claim' ? 'No linked claims'
          : ['paper', 'specialist', 'trial', 'resource'].includes(kind)
            ? liveState === 'loading' ? 'Loading…' : liveState === 'partial' ? 'Unknown / partial' : liveState === 'loaded' ? 'None retrieved' : 'Not searched'
            : 'Not loaded';
        return <option key={kind} value={kind}>{label} ({status})</option>;
      })}</select>}
      {selected && <button className={styles.back} onClick={() => { setSelectedId(''); setPage(0); setFilter('all'); }}><ArrowLeft size={12}/>{region.label}</button>}

    </div>
    {loading && <div className={styles.status} role="status"><LoaderCircle size={18} className={styles.spinner}/><span>Connecting {region.label.toLowerCase()} to the repository…</span></div>}
    {error && <div className={styles.status} role="alert"><p>{error}</p><button onClick={() => setRetry(value => value + 1)}>Retry regional data</button></div>}
    {data && !loading && <>
      <svg className={styles.edges} aria-hidden="true">
        {callouts.map(tag => <line key={tag.id} data-tone={widgetTone(tag.id)} data-selected={selectedId === tag.id} x1={tag.anchor.x} y1={tag.anchor.y} x2={tag.end.x} y2={tag.end.y}><title>Associated with {region.label}</title></line>)}
      </svg>
      {callouts.map(tag => {
        const node = visible.find(item => item.id === tag.id)!;
        const path = paths.get(node.id) ?? [];
        return <div key={node.id} className={styles.callout} data-side={tag.side} data-tone={widgetTone(node.id)} style={{ left: tag.x, top: tag.y, width: tag.width, minHeight: tag.height }}>
          <button className={styles.node} style={{ height: tag.height }} onPointerDown={event => { if (event.button === 0) event.currentTarget.setPointerCapture(event.pointerId); }} onClick={() => select(node)} title={`${node.label} · ${kindLabel(node)}${path.length ? ': ' + path.map((edge: { relation: string }) => relationLabel(edge.relation)).join(' → ') : ''}`} aria-label={`Explore ${node.label}, ${kindLabel(node)}`} aria-pressed={selectedId === node.id}>
            <span className={styles.nodeTitle}>{node.label}</span>
          </button>
          {selected?.id === node.id && <details className={styles.recordDetails}>
            <summary>Details & source</summary>
            <div className={styles.details}>
        <span className={styles.eyebrow}>{kindLabel(selected)} · {selected.id}</span><h3>{selected.label}</h3>
        {selected.claim ? <section aria-label="Publication claim">
          <p>{selected.claim.reviewed ? 'Reviewed claim' : 'Unreviewed extracted claim'} — not proof of a shared disease mechanism.</p>
          <blockquote>{selected.claim.passage}</blockquote>
          <p>{selected.claim.subject} → {selected.claim.relationship} → {selected.claim.object}</p>
          <p>Direction: {selected.claim.direction}. Assertion: {selected.claim.polarity}.</p>
          <p>Study type: {selected.claim.study_type || 'unknown'}</p>
          <p>{Object.entries(selected.claim.context).map(([key, value]) => `${key}: ${value}`).join(' · ')}</p>
          <p>Publication location: {JSON.stringify(selected.claim.locator)}</p>
          {selected.claim.limitations.map((text, index) => <p key={index}>{text}</p>)}
        </section> : <p>{selected.description || 'No description supplied by this source.'}</p>}
        {selected.access && <p>{selected.access}</p>}
        {selected.location && <p>{selected.location}</p>}{selected.email && <p>{selected.email}</p>}{selected.phone && <p>{selected.phone}</p>}
        <div className={styles.sourceLinks}>{selected.url && <a href={selected.url} target="_blank" rel="noreferrer">Open original source <ArrowUpRight size={13}/></a>}<span>{selected.providers.join(' · ')}</span></div>
        {selected.kind === 'disease' && /^(OMIM|ORPHA|MONDO|DECIPHER):\d+$/.test(selected.id) && <button className={styles.expand} disabled={expanding || expanded.includes(selected.id)} onClick={() => expand(selected)}>{expanding ? <><LoaderCircle size={14} className={styles.spinner}/>Loading connected research…</> : expanded.includes(selected.id) ? 'Connected research loaded' : 'Load genes, papers & specialist resources'}</button>}
        {selected.kind === 'disease' && /^(OMIM|ORPHA|MONDO|DECIPHER):\d+$/.test(selected.id) && <button className={styles.expand} disabled={expanding} onClick={() => expand(selected, true)}>Search papers & resources</button>}
        {expandNotice && <p className={styles.notice} role="status">{expandNotice}</p>}
        <button className={styles.ask} onClick={() => onAsk(`Explain ${selected.label} (${selected.id}) and its recorded connections to ${region.label}. Include sources.`)}>Ask about this record <ArrowUpRight size={13}/></button>

            </div>
          </details>}
        </div>;
      })}
      {!visible.length && <div className={styles.empty} role="status">{!data.total ? 'No matching disease records in this regional dataset.' : `No ${filter === 'specialist' ? 'doctor or centre' : filter} records loaded yet.`}{data.total > 0 && <p>Select a disease and load its connected research to explore more record types.</p>}</div>}
      <div className={styles.pagination} style={{ maxWidth: viewport.width - 40 }}>
        {selected && <button onClick={() => { setSelectedId(''); setPage(0); setFilter('all'); }}><ArrowLeft size={12}/>Region</button>}
        <button aria-label="Previous graph nodes" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={14}/></button>
        <span>{filtered.length ? `${currentPage * recordPageSize + 1}–${Math.min(filtered.length, (currentPage + 1) * recordPageSize)} of ${filtered.length} nodes` : '0 nodes'}</span>
        <button aria-label="Next graph nodes" disabled={currentPage >= lastPage} onClick={() => setPage(currentPage + 1)}><ChevronRight size={14}/></button>
        <span className={styles.separator}/>
        <button aria-label="Previous disease records" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 6))}><ChevronLeft size={14}/></button>
        <span>Diseases {data.total ? offset + 1 : 0}–{Math.min(offset + 6, data.total)} / {data.total.toLocaleString()}</span>
        <button aria-label="Next disease records" disabled={data.nextOffset === null} onClick={() => setOffset(data.nextOffset!)}><ChevronRight size={14}/></button>
      </div>
      <div className={styles.provenance}>Counts show this loaded graph, including the selected record—not repository totals. Zero means none linked in this snapshot, not none exist. HPO · {data.datasetVersion}</div>

    </>}
  </div>;
}
