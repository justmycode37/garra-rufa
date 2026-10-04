'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowUpRight, ChevronLeft, ChevronRight, LoaderCircle, Search, X } from 'lucide-react';
import { atlasGraphSchema, atlasNodeKind, atlasPaths, atlasRegions, mergeAtlasGraphs, repositoryGraphSchema, type AtlasGraphData, type AtlasNode } from '@/lib/atlas-graph';
import type { AnatomyFrame } from '@/lib/anatomy-scene';
import styles from './AtlasKnowledgeGraph.module.css';

const cache = new Map<string, { at: number; data: AtlasGraphData }>();
const filters = [['all', 'All'], ['disease', 'Diseases'], ['phenotype', 'Phenotypes'], ['gene', 'Genes'], ['specialist', 'Doctors & centres'], ['paper', 'Papers'], ['trial', 'Trials'], ['resource', 'Resources']] as const;
const kindLabel = (node: AtlasNode) => node.kind.replaceAll('_', ' ');
const relationLabel = (relation: string) => relation.replaceAll('_', ' ');

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
  const [expanded, setExpanded] = useState<string[]>([]);
  const expandRequest = useRef<AbortController | null>(null);

  useEffect(() => { const timer = setTimeout(() => { setFilterQuery(query); setOffset(0); }, 300); return () => clearTimeout(timer); }, [query]);
  useEffect(() => {
    const abort = new AbortController();
    expandRequest.current?.abort(); setExpanding(false); setExpandNotice(''); setExpanded([]);
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
  const hasSidePanel = !!selected && viewport.width >= 900;
  const graphWidth = viewport.width - (hasSidePanel ? 330 : 0);
  const top = viewport.width < 600 ? 164 : 142, bottom = Math.max(top + 90, viewport.height - 210);
  const perColumn = Math.max(1, Math.min(6, Math.floor((bottom - top) / 62)));
  const pageSize = perColumn * 2;
  const lastPage = Math.max(0, Math.ceil(filtered.length / pageSize) - 1);
  const currentPage = Math.min(page, lastPage);
  const visible = filtered.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  const nodeWidth = Math.min(220, graphWidth * .31);
  const root = { x: Math.max(nodeWidth + 36, Math.min(graphWidth - nodeWidth - 36, frame.regionAnchor?.x ?? graphWidth / 2)), y: Math.max(top + 32, Math.min(bottom - 32, frame.regionAnchor?.y ?? (top + bottom) / 2)) };
  const positions = new Map<string, { x: number; y: number }>([[rootId, root]]);
  visible.forEach((node, index) => {
    const side = index % 2, row = Math.floor(index / 2), rows = Math.ceil(visible.length / 2);
    positions.set(node.id, { x: side ? graphWidth - nodeWidth / 2 - 22 : nodeWidth / 2 + 22, y: top + (bottom - top) * (row + .5) / rows });
  });

  function select(node: AtlasNode) {
    expandRequest.current?.abort(); setExpanding(false);
    setSelectedId(node.id); setFilter('all'); setPage(0); setExpandNotice('');
    if (node.kind === 'disease' && /^(OMIM|ORPHA|MONDO|DECIPHER):\d+$/.test(node.id)) void expand(node);
  }
  async function expand(node: AtlasNode) {
    if (expanded.includes(node.id)) return;
    const abort = new AbortController(); expandRequest.current?.abort(); expandRequest.current = abort;
    setExpanding(true); setExpandNotice('');
    try {
      const response = await fetch(`/api/atlas/expand?${new URLSearchParams({ entity: node.id, label: node.label.slice(0, 200) })}`, { signal: abort.signal });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Connected records could not be loaded.');
      const graph = repositoryGraphSchema.parse(body.graph);
      if (abort.signal.aborted) return;
      setData(current => current ? mergeAtlasGraphs(current, [graph]) : current);
      const unavailable: string[] = Array.isArray(body.unavailableProviders) ? body.unavailableProviders.filter((item: unknown) => typeof item === 'string') : [];
      if (!unavailable.length) setExpanded(previous => [...previous, node.id]);
      setExpandNotice(unavailable.length ? `Some sources are unavailable: ${unavailable.join(', ')}. Retrieved connections are shown; you can retry.` : 'Connected records loaded. Only source-listed people appear as doctors.');
    } catch (error) { if (!abort.signal.aborted) setExpandNotice(error instanceof Error ? error.message : 'Connected records could not be loaded.'); }
    finally { if (!abort.signal.aborted) setExpanding(false); }
  }

  return <div className={styles.layer} aria-label={`${region.label} knowledge graph`}>
    <div className={styles.toolbar}>
      <div className={styles.heading}><span className={styles.liveDot}/><strong>{region.label}</strong><span>{loading ? 'Loading regional records…' : data ? `${data.total.toLocaleString()} disease records` : 'Regional knowledge'}</span></div>
      <label className={styles.search}><Search size={13}/><input aria-label="Filter regional disease records" value={query} onChange={event => setQuery(event.target.value)} placeholder="Find a disease in this region" maxLength={120}/>{query && <button aria-label="Clear disease filter" onClick={() => setQuery('')}><X size={12}/></button>}</label>
      {data && <div className={styles.filters} role="group" aria-label="Graph record types">{filters.map(([kind, label]) => {
        const count = records.filter(node => matches(node, kind)).length;
        return <button key={kind} aria-pressed={filter === kind} onClick={() => { setFilter(kind); setPage(0); }}>{label}<span>{count}</span></button>;
      })}</div>}
    </div>
    {loading && <div className={styles.status} role="status"><LoaderCircle size={18} className={styles.spinner}/><span>Connecting {region.label.toLowerCase()} to the repository…</span></div>}
    {error && <div className={styles.status} role="alert"><p>{error}</p><button onClick={() => setRetry(value => value + 1)}>Retry regional data</button></div>}
    {data && !loading && <>
      <svg className={styles.edges} aria-hidden="true">
        {visible.map(node => {
          const path = paths.get(node.id)!;
          const end = positions.get(node.id)!;
          // Draw to the nearest visible point along the real path; a dashed
          // line explicitly collapses intermediate records, never invents an edge.
          let from = root, hidden = path.length, walk = rootId;
          for (let index = 0; index < path.length - 1; index++) {
            walk = path[index].from === walk ? path[index].to : path[index].from;
            if (positions.has(walk)) { from = positions.get(walk)!; hidden = path.length - index - 1; }
          }
          return <g key={node.id}><line x1={from.x} y1={from.y} x2={end.x} y2={end.y} strokeDasharray={hidden > 1 ? '3 5' : undefined}/><circle cx={end.x} cy={end.y} r="3"/><title>{path.map((edge: { relation: string }) => relationLabel(edge.relation)).join(' → ')}</title></g>;
        })}
        {data.edges.filter(edge => edge.from !== rootId && edge.to !== rootId && positions.has(edge.from) && positions.has(edge.to)).slice(0, 80).map((edge, index) => <line key={`cross-${index}`} className={styles.crossEdge} x1={positions.get(edge.from)!.x} y1={positions.get(edge.from)!.y} x2={positions.get(edge.to)!.x} y2={positions.get(edge.to)!.y}><title>{relationLabel(edge.relation)} · {edge.source}</title></line>)}
      </svg>
      <button className={styles.root} style={{ left: root.x, top: root.y }} onClick={() => { setSelectedId(''); setPage(0); }} title="Return to the regional graph"><span/>{selected ? selected.label : region.label}<small>{selected ? kindLabel(selected) : 'Anatomical region'}</small></button>
      {visible.map(node => {
        const position = positions.get(node.id)!;
        const path = paths.get(node.id)!;
        return <button key={node.id} className={styles.node} data-kind={atlasNodeKind(node)} style={{ left: position.x, top: position.y, width: nodeWidth }} onClick={() => select(node)} title={node.label} aria-label={`Explore ${node.label}, ${kindLabel(node)}`}>
          <span className={styles.nodeTitle}>{node.label}</span><small><i/>{kindLabel(node)}<span>· {path.length === 1 ? relationLabel(path[0].relation) : `${path.length} linked relationships`}</span></small>
        </button>;
      })}
      {!visible.length && <div className={styles.empty} role="status">{!data.total ? 'No matching disease records in this regional dataset.' : `No ${filter === 'specialist' ? 'doctor or centre' : filter} records loaded yet.`}{data.total > 0 && <p>Select a disease and load its connected research to explore more record types.</p>}</div>}
      <div className={styles.pagination} style={{ maxWidth: graphWidth - 40 }}>
        {selected && <button onClick={() => { setSelectedId(''); setPage(0); setFilter('all'); }}><ArrowLeft size={12}/>Region</button>}
        <button aria-label="Previous graph nodes" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={14}/></button>
        <span>{filtered.length ? `${currentPage * pageSize + 1}–${Math.min(filtered.length, (currentPage + 1) * pageSize)} of ${filtered.length} nodes` : '0 nodes'}</span>
        <button aria-label="Next graph nodes" disabled={currentPage >= lastPage} onClick={() => setPage(currentPage + 1)}><ChevronRight size={14}/></button>
        <span className={styles.separator}/>
        <button aria-label="Previous disease records" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 6))}><ChevronLeft size={14}/></button>
        <span>Diseases {data.total ? offset + 1 : 0}–{Math.min(offset + 6, data.total)} / {data.total.toLocaleString()}</span>
        <button aria-label="Next disease records" disabled={data.nextOffset === null} onClick={() => setOffset(data.nextOffset!)}><ChevronRight size={14}/></button>
      </div>
      <div className={styles.provenance}>HPO · {data.datasetVersion} <span>Dashed lines follow multiple recorded relationships</span></div>
      {selected && <aside className={styles.details} aria-label="Selected graph record">
        <button className={styles.close} aria-label="Close record details" onClick={() => setSelectedId('')}><X size={16}/></button>
        <span className={styles.eyebrow}>{kindLabel(selected)} · {selected.id}</span><h3>{selected.label}</h3>
        <p>{selected.description || 'No description supplied by this source.'}</p>
        {selected.location && <p>{selected.location}</p>}{selected.email && <p>{selected.email}</p>}{selected.phone && <p>{selected.phone}</p>}
        <div className={styles.sourceLinks}>{selected.url && <a href={selected.url} target="_blank" rel="noreferrer">Open original source <ArrowUpRight size={13}/></a>}<span>{selected.providers.join(' · ')}</span></div>
        {selected.kind === 'disease' && /^(OMIM|ORPHA|MONDO|DECIPHER):\d+$/.test(selected.id) && <button className={styles.expand} disabled={expanding || expanded.includes(selected.id)} onClick={() => expand(selected)}>{expanding ? <><LoaderCircle size={14} className={styles.spinner}/>Loading connected research…</> : expanded.includes(selected.id) ? 'Connected research loaded' : 'Load genes, papers & specialist resources'}</button>}
        {expandNotice && <p className={styles.notice} role="status">{expandNotice}</p>}
        <button className={styles.ask} onClick={() => onAsk(`Explain ${selected.label} (${selected.id}) and its recorded connections to ${region.label}. Include sources.`)}>Ask about this record <ArrowUpRight size={13}/></button>
        <h4>Recorded connections</h4>
        <div className={styles.relations}>{data.edges.filter(edge => edge.from === selected.id || edge.to === selected.id).map((edge, index) => {
          const other = data.nodes.find(node => node.id === (edge.from === selected.id ? edge.to : edge.from));
          if (!other) return null;
          return <button key={index} onClick={() => select(other)}><small>{relationLabel(edge.relation)} · {edge.source}</small><span>{other.label}</span>{edge.evidence.length > 0 && <small>{edge.evidence.slice(0, 3).join(' · ')}{edge.evidence.length > 3 ? ` +${edge.evidence.length - 3}` : ''}</small>}</button>;
        })}</div>
      </aside>}
    </>}
  </div>;
}
