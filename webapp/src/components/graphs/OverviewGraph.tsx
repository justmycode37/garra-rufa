'use client';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { ArrowUpRight, ChevronRight, LoaderCircle, Search, Sparkles } from 'lucide-react';
import { blend, tint, type GraphLink, type GraphNode } from '@/lib/force-canvas';
import { SECTION_COLORS, safeUrl, type PresentItem, type PresentView } from '@/lib/graph-views';
import { candidateQuery } from '@/lib/graph-builds';
import { GraphMessage, loadView, useGraphRun } from './GraphsShell';
import { useForceCanvas, ZoomControls } from './useForceCanvas';
import { ItemFacts } from './NodeFacts';
import styles from './Graphs.module.css';

const MAX_SHOWN = 15; // entries drawn when a topic opens; all are listed in the panel
type Selection = { type: 'focus' } | { type: 'group'; id: string } | { type: 'item'; id: string } | null;

export default function OverviewGraph() {
  const { run, loading } = useGraphRun();
  const [data, setData] = useState<PresentView>();
  const [error, setError] = useState('');
  useEffect(() => {
    setData(undefined); setError('');
    if (!run?.presentUrl) return;
    let live = true;
    loadView<PresentView>(run.presentUrl).then(d => live && setData(d)).catch(e => live && setError(e.message));
    return () => { live = false; };
  }, [run?.presentUrl]);
  if (loading) return <GraphMessage busy>Loading graphs…</GraphMessage>;
  if (!run) return <GraphMessage>No graphs yet. Build one from a disease, gene, or symptom query.</GraphMessage>;
  if (!run.presentUrl) return <GraphMessage busy={run.build?.state === 'running' || run.build?.state === 'queued'}>The overview of {run.label} is not ready yet.</GraphMessage>;
  if (error) return <GraphMessage>{error}</GraphMessage>;
  if (!data) return <GraphMessage busy>Loading {run.label}…</GraphMessage>;
  return <Overview key={run.id} data={data}/>;
}

function Overview({ data }: { data: PresentView }) {
  const F = data.focus, ITEMS = data.items;
  const SECTION = useMemo(() => Object.fromEntries(data.sections.map(s => [s.id, { ...s, color: SECTION_COLORS[s.id] ?? s.color }])), [data]);
  const GROUP = useMemo(() => Object.fromEntries(data.groups.map(g => [g.id, g])), [data]);
  const [enabled, setEnabled] = useState(() => new Set(data.sections.map(s => s.id)));
  // a symptom / gene search (present.query_view): its candidate diseases are the point, so
  // they start open and are drawn larger, labelled and nearer the centre
  const isSearch = data.sections.some(s => s.id === 'candidates');
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(data.groups.filter(g => g.section === 'candidates').map(g => g.id)));
  const [extra, setExtra] = useState<Set<string>>(() => new Set());
  const [showLinks, setShowLinks] = useState(true);
  const [selection, setSelection] = useState<Selection>({ type: 'focus' });
  const [query, setQuery] = useState('');

  const linksOf = useMemo(() => {
    const out: Record<string, { other: string; label: string }[]> = {};
    for (const l of data.links) {
      (out[l.a] ||= []).push({ other: l.b, label: l.label });
      (out[l.b] ||= []).push({ other: l.a, label: l.label });
    }
    return out;
  }, [data]);

  // Colours merge along connections: an entry or topic takes on the colours of the
  // topics it is connected to, weighted by how many connections lead there.
  const colors = useMemo(() => {
    const sectionMix = (own: string, ids: string[], ownWeight: number) => {
      const counts = new Map<string, number>();
      for (const id of ids) for (const c of linksOf[id] ?? []) {
        const s = ITEMS[c.other]?.section;
        if (s && s !== own) counts.set(s, (counts.get(s) ?? 0) + 1);
      }
      const others = [...counts].sort((a, b) => b[1] - a[1]).slice(0, 3);
      return {
        mixed: blend([{ color: SECTION[own].color, weight: ownWeight }, ...others.map(([s, n]) => ({ color: SECTION[s].color, weight: Math.min(3, n) }))]),
        stops: [SECTION[own].color, ...others.map(([s]) => SECTION[s].color)],
      };
    };
    const item: Record<string, { mixed: string; stops: string[] }> = {};
    for (const it of Object.values(ITEMS)) item[it.id] = sectionMix(it.section, [it.id], 3);
    const group: Record<string, { mixed: string; stops: string[] }> = {};
    for (const g of data.groups) group[g.id] = sectionMix(g.section, g.items, Math.max(4, g.items.length));
    return { item, group };
  }, [data, ITEMS, SECTION, linksOf]);

  // topics sit in section order on two staggered rings, sized so their pills fit
  const ring = Math.max(240, data.groups.reduce((s, g) => s + Math.min(34, g.label.length) * 7.5 + 60, 0) * 0.62 / (2 * Math.PI));
  const ringPos = useMemo(() => Object.fromEntries(data.groups.map((g, i) => {
    const a = 2 * Math.PI * i / data.groups.length - Math.PI / 2, out = data.groups.length > 10 && i % 2 ? 1.3 : 1;
    return [g.id, { x: Math.cos(a) * out, y: Math.sin(a) * out }];
  })), [data]);

  const shownItems = (gid: string) => {
    const g = GROUP[gid];
    if (!expanded.has(gid)) return g.items.filter(i => extra.has(i));
    return g.items.filter((i, k) => k < MAX_SHOWN || extra.has(i));
  };
  const visibleOf = (id: string) => {
    const it = ITEMS[id];
    if (!it || !enabled.has(it.section)) return null;
    return shownItems(it.group_id).includes(id) ? id : it.group_id;
  };

  const { canvas, engine } = useForceCanvas({
    chargeStrength: -220, collidePadding: 8, labelZoom: 1.6, insets: { left: 350, right: 380 },
    onClick: hit => {
      const id = hit?.node?.id;
      if (!id) { setSelection(null); return; }
      if (GROUP[id]) {
        setExpanded(s => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
        setSelection({ type: 'group', id });
      } else if (ITEMS[id]) setSelection({ type: 'item', id });
      else if (id === F.id) setSelection({ type: 'focus' });
    },
  });

  // graph model -> engine
  useEffect(() => {
    if (!engine) return;
    const nodes: GraphNode[] = [], links: GraphLink[] = [];
    const allSections = data.sections.filter(s => enabled.has(s.id)).map(s => SECTION[s.id].color);
    nodes.push({ id: F.id, label: F.label, r: 30, fill: allSections.length ? allSections : ['#141314'], stroke: '#fffdfe', strokeWidth: 4, pinLabel: true, priority: 10, fixed: true, x: 0, y: 0, fx: 0, fy: 0, charge: -900 });
    for (const g of data.groups) {
      if (!enabled.has(g.section)) continue;
      const c = colors.group[g.id], p = ringPos[g.id], open = expanded.has(g.id);
      const cand = g.section === 'candidates', reach = cand ? 0.72 : 1;
      const label = !cand ? g.label : /^Match /.test(g.label) ? g.label.replace(/^Match/, 'Candidates matching') : 'Candidate diseases';
      nodes.push({ id: g.id, label: label.length > 34 ? label.slice(0, 32) + '…' : label, badge: String(g.count), shape: 'pill', r: cand ? 22 : 18,
        fill: c.stops.length > 1 ? c.stops.map(s => tint(s, 0.42)) : tint(c.mixed, 0.42), stroke: open || cand ? '#141314' : c.mixed, strokeWidth: cand ? 2.4 : open ? 2 : 1.2,
        tx: p.x * ring * reach, ty: p.y * ring * reach, pull: 0.09, charge: -700, priority: cand ? 9 : 5, pinLabel: cand });
      links.push({ id: `s:${g.id}`, source: F.id, target: g.id, colors: [SECTION[g.section].color, c.mixed], width: cand ? 4 : 2.6, opacity: cand ? 0.8 : 0.55, distance: ring * 0.9 * reach, strength: 0.03 });
      for (const iid of shownItems(g.id)) {
        // symptoms: everyday name, size by how often they occur, a gold ring for hallmark signs
        const ic = colors.item[iid], it = ITEMS[iid], mark = !cand && !!it.hallmark;
        nodes.push({ id: iid, label: it.lay ?? it.label, r: cand ? 14 : it.freq != null ? 6 + 5 * it.freq : 8, fill: ic.stops.length > 1 ? ic.stops : ic.mixed,
          stroke: cand ? '#141314' : mark ? '#c9a52c' : '#fffdfe', strokeWidth: cand ? 2 : mark ? 3 : 1.5, spawn: g.id,
          tx: p.x * ring * 1.55 * reach, ty: p.y * ring * 1.55 * reach, pull: 0.015, charge: cand ? -320 : -140,
          priority: cand ? 8 : 1 + Math.min(1, ITEMS[iid].score / 3), pinLabel: cand });
        links.push({ id: `m:${iid}`, source: g.id, target: iid, colors: [c.mixed, ic.mixed], width: cand ? 2.2 : 1.4, opacity: cand ? 0.8 : 0.6, distance: cand ? 95 : 70, strength: 0.6 });
      }
    }
    if (showLinks) {
      const agg = new Map<string, { a: string; b: string; n: number }>();
      for (const l of data.links) {
        const a = visibleOf(l.a), b = visibleOf(l.b);
        if (!a || !b || a === b) continue;
        const key = a < b ? `${a}|${b}` : `${b}|${a}`;
        const e = agg.get(key) ?? { a, b, n: 0 };
        e.n++; agg.set(key, e);
      }
      const colorOf = (id: string) => (GROUP[id] ? colors.group[id].mixed : colors.item[id].mixed);
      for (const [key, e] of agg) links.push({ id: `x:${key}`, source: e.a, target: e.b, colors: [colorOf(e.a), colorOf(e.b)],
        width: Math.min(5, 0.9 + Math.log2(e.n)), opacity: 0.5, dash: [5, 5], distance: 220, strength: 0.004, curve: 0.12 });
    }
    engine.setGraph(nodes, links, { energy: 0.5 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine, data, enabled, expanded, extra, showLinks, colors]);
  useEffect(() => { engine?.fitWhenSettled(); }, [engine]);
  useEffect(() => { engine?.setSelected(selection && selection.type !== 'focus' ? selection.id : selection ? F.id : null); }, [engine, selection, F.id]);

  function selectItem(iid: string) {
    const it = ITEMS[iid];
    if (!it) return;
    if (!enabled.has(it.section)) setEnabled(s => new Set(s).add(it.section));
    setExpanded(s => new Set(s).add(it.group_id));
    if (!GROUP[it.group_id].items.slice(0, MAX_SHOWN).includes(iid)) setExtra(s => new Set(s).add(iid));
    setSelection({ type: 'item', id: iid });
    setTimeout(() => engine?.focus(iid, 1.6), 350);
  }

  const secCount = (s: string) => data.groups.filter(g => g.section === s).reduce((n, g) => n + g.count, 0);
  const q = query.trim().toLowerCase();
  const hits = q ? Object.values(ITEMS).filter(it => it.label.toLowerCase().includes(q) || it.lay?.toLowerCase().includes(q)).slice(0, 20) : [];
  const ctx = { ITEMS, GROUP, SECTION, linksOf, colors, selectItem, selectGroup: (id: string) => setSelection({ type: 'group', id }) };

  return <div className={styles.stage}>
    <canvas ref={canvas} className={styles.canvas} aria-label={`Overview graph of ${F.label}: ${data.groups.length} topics, ${Object.keys(ITEMS).length} entries. The panels list the same content.`} role="img"/>
    <ZoomControls engine={engine}/>
    <aside className={`${styles.panel} ${styles.left}`} aria-label="Disease summary and topics">
      <span className="eyebrow">{isSearch ? 'SEARCH OVERVIEW' : 'DISEASE OVERVIEW'}</span>
      <h1 className={styles.panelTitle}>{F.label}</h1>
      {F.facts.length > 0 && <dl className={styles.facts}>{F.facts.map(f => <div key={f.label}><dt>{f.label}</dt><dd>{f.value}</dd></div>)}</dl>}
      {F.description && <Clamp text={F.description}/>}
      {isSearch && <>
        <h2 className={styles.h2}>Candidate diseases</h2>
        <ol className={`${styles.list} ${styles.candidates}`}>{Object.values(ITEMS).filter(it => it.section === 'candidates').sort((a, b) => b.score - a.score).map(it => <li key={it.id}>
          <button onClick={() => selectItem(it.id)} aria-pressed={selection?.type === 'item' && selection.id === it.id}>
            <span className={styles.swatch} style={{ background: colors.item[it.id].mixed }}/><span>{it.label}<small>{GROUP[it.group_id].label} · score {it.score.toFixed(1)}</small></span>
          </button></li>)}</ol>
      </>}
      {F.synonyms.length > 0 && <p className="fineprint">Also known as: {F.synonyms.join('; ')}</p>}
      <LinkRow links={F.links}/>
      <h2 className={styles.h2}>Topics</h2>
      <div className={styles.toggles}>{data.sections.map(s => <label key={s.id} className={styles.toggle}>
        <input type="checkbox" checked={enabled.has(s.id)} onChange={e => setEnabled(prev => { const n = new Set(prev); if (e.target.checked) n.add(s.id); else n.delete(s.id); return n; })}/>
        <span className={styles.dot} style={{ background: SECTION[s.id].color }}/>{s.label}<span className={styles.count}>{secCount(s.id)}</span>
      </label>)}</div>
      <div className={styles.buttons}>
        <button className="secondary" onClick={() => setExpanded(new Set(data.groups.map(g => g.id)))}>Open all</button>
        <button className="secondary" onClick={() => { setExpanded(new Set()); setExtra(new Set()); }}>Close all</button>
      </div>
      <label className={styles.toggle}><input type="checkbox" checked={showLinks} onChange={e => setShowLinks(e.target.checked)}/>Connections between topics</label>
      <label className={styles.search}><Search size={14}/><input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Symptom, gene, organisation…" aria-label="Search entries"/></label>
      {hits.length > 0 && <div className={styles.list}>{hits.map(it => <button key={it.id} onClick={() => selectItem(it.id)}>
        <span className={styles.swatch} style={{ background: colors.item[it.id].mixed }}/><span>{it.label}<small>{GROUP[it.group_id].label}</small></span>
      </button>)}</div>}
      <p className="fineprint">Click a topic to open it. Entries and topics take on the colours of the topics they connect to; dashed lines show those connections (a gene and its symptoms, a trial and the drug it tests).</p>
    </aside>
    <aside className={`${styles.panel} ${styles.right}`} aria-label="Selection details" aria-live="polite">
      {selection?.type === 'group' && GROUP[selection.id] ? <GroupDetail gid={selection.id} {...ctx}/>
        : selection?.type === 'item' && ITEMS[selection.id] ? <ItemDetail iid={selection.id} {...ctx}/>
        : selection?.type === 'focus' ? <FocusDetail data={data} isSearch={isSearch}/>
        : <p className="muted">Click a topic or entry in the graph.</p>}
      {data.notes.length > 0 && selection?.type === 'focus' && <p className="fineprint">{data.notes.join(' · ')}</p>}
    </aside>
  </div>;
}

type DetailCtx = {
  ITEMS: Record<string, PresentItem>; GROUP: Record<string, PresentView['groups'][number]>;
  SECTION: Record<string, { label: string; color: string }>; linksOf: Record<string, { other: string; label: string }[]>;
  colors: { item: Record<string, { mixed: string }> }; selectItem: (id: string) => void; selectGroup: (id: string) => void;
};

function FocusDetail({ data, isSearch }: { data: PresentView; isSearch: boolean }) {
  return <>
    <span className="eyebrow">{isSearch ? 'THE SEARCH' : 'THE DISEASE'}</span>
    <h2 className={styles.detailTitle}>{data.focus.label}</h2>
    {data.focus.description ? <p className={styles.body}>{data.focus.description}</p> : <p className="muted">No description in the sources.</p>}
    <p className="fineprint">{Object.keys(data.items).length} entries in {data.groups.length} topics, {data.links.length} connections between entries. Sources: Orphanet, MONDO, HPO, ClinicalTrials.gov, patient organisation directories and more.</p>
  </>;
}

function GroupDetail({ gid, ITEMS, GROUP, SECTION, linksOf, colors, selectItem, selectGroup }: { gid: string } & DetailCtx) {
  const g = GROUP[gid];
  return <>
    <span className="eyebrow" style={{ color: SECTION[g.section].color }}>{SECTION[g.section].label.toUpperCase()}</span>
    <h2 className={styles.detailTitle}>{g.label}</h2>
    <p className="muted">{g.count} {g.count === 1 ? 'entry' : 'entries'}</p>
    <div className={styles.list}>{g.items.map(iid => {
      const it = ITEMS[iid];
      const sub = [it.frequency, it.status, it.phase && `phase ${it.phase}`, ...(it.notes || [])].filter(Boolean).join(' · ');
      return <button key={iid} onClick={() => selectItem(iid)}><span className={styles.swatch} style={{ background: colors.item[iid].mixed }}/><span>{it.label}{sub && <small>{sub}</small>}</span></button>;
    })}</div>
    <Connections ids={g.items} {...{ ITEMS, GROUP, SECTION, linksOf, colors, selectItem, selectGroup }}/>
  </>;
}

function ItemDetail({ iid, ITEMS, GROUP, SECTION, linksOf, colors, selectItem, selectGroup }: { iid: string } & DetailCtx) {
  const it = ITEMS[iid], g = GROUP[it.group_id];
  const facts = ([
    it.frequency && ['How often', it.frequency], it.status && ['Status', it.status], it.phase && ['Trial phase', `phase ${it.phase}`],
    it.country && ['Country', it.country], it.sponsors && ['Sponsor', it.sponsors.join(', ')],
    it.variants && ['Variants', Object.entries(it.variants).map(([k, v]) => `${v} ${k}`).join(', ')],
    it.sources?.length && ['Sources', it.sources.join(', ')],
  ].filter(Boolean)) as [string, string][];
  return <>
    <button className={styles.crumb} onClick={() => selectGroup(g.id)}>{SECTION[it.section].label}<ChevronRight size={12}/>{g.label}</button>
    <h2 className={styles.detailTitle}><span className={styles.swatch} style={{ background: colors.item[iid].mixed }}/>{it.label}</h2>
    {facts.length > 0 && <dl className={styles.facts}>{facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>}
    {it.notes.map(n => <p key={n} className="muted">{n}</p>)}
    <ItemFacts item={it}/>
    {it.section === 'candidates' && <BuildCandidate name={it.label}/>}
    {it.description && <Clamp text={it.description}/>}
    <LinkRow links={it.links}/>
    <Connections ids={[iid]} {...{ ITEMS, GROUP, SECTION, linksOf, colors, selectItem, selectGroup }}/>
  </>;
}

/** A full disease graph for one candidate of a symptom search, built from its name only. */
function BuildCandidate({ name }: { name: string }) {
  const { canBuild, startGraph } = useGraphRun();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => { setBusy(false); setMessage(''); }, [name]);
  const graphQuery = candidateQuery(name);
  if (canBuild.signedIn === false) return <p className={styles.notice}><Link href="/?entry=signup&role=researcher">Sign in</Link> to build a graph of {name}.</p>;
  if (canBuild.signedIn !== null && !canBuild.enabled) return <p className="fineprint">{canBuild.notice || 'Building graphs is unavailable right now.'}</p>;
  return <div className={styles.candidateBuild}>
    <button className="primary" disabled={busy || graphQuery.length < 2 || canBuild.signedIn === null} onClick={async () => {
      setBusy(true); setMessage('');
      const { error, cached } = await startGraph(graphQuery);
      setMessage(error || (cached ? 'Opened from the cache.' : 'Building. The graph opens here as soon as its overview is ready.'));
      if (error) setBusy(false);
    }}>{busy ? <LoaderCircle size={15} className={styles.spin}/> : <Sparkles size={15}/>}Build a graph of this disease</button>
    {message && <p className="fineprint" role="status">{message}</p>}
  </div>;
}

function Connections({ ids, ITEMS, GROUP, SECTION, linksOf, colors, selectItem }: { ids: string[] } & DetailCtx) {
  const own = new Set(ids), byGroup = new Map<string, Map<string, Set<string>>>();
  for (const id of ids) for (const c of linksOf[id] ?? []) {
    if (own.has(c.other) || !ITEMS[c.other]) continue;
    const gid = ITEMS[c.other].group_id;
    if (!byGroup.has(gid)) byGroup.set(gid, new Map());
    const m = byGroup.get(gid)!;
    m.set(c.other, (m.get(c.other) ?? new Set()).add(c.label));
  }
  if (!byGroup.size) return null;
  return <>
    <h3 className={styles.h2}>Connected to</h3>
    {[...byGroup].sort((a, b) => b[1].size - a[1].size).map(([gid, m]) => <div key={gid} className={styles.connGroup}>
      <span className={styles.connHead}><span className={styles.dot} style={{ background: SECTION[GROUP[gid].section].color }}/>{GROUP[gid].label}</span>
      <div className={styles.list}>{[...m].slice(0, 12).map(([oid, labels]) => <button key={oid} onClick={() => selectItem(oid)}>
        <span className={styles.swatch} style={{ background: colors.item[oid].mixed }}/><span>{ITEMS[oid].label}<small>{[...labels].join(', ')}</small></span>
      </button>)}{m.size > 12 && <p className="fineprint">… {m.size - 12} more</p>}</div>
    </div>)}
  </>;
}

function Clamp({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 360;
  return <p className={styles.body}>{open || !long ? text : text.slice(0, 340).replace(/\s+\S*$/, '') + '…'}{long && <button className={styles.more} onClick={() => setOpen(o => !o)}>{open ? 'Show less' : 'Read more'}</button>}</p>;
}

export function LinkRow({ links }: { links: { label: string; url: string }[] }): ReactNode {
  const ok = links.filter(l => safeUrl(l.url));
  if (!ok.length) return null;
  return <div className={styles.links}>{ok.map(l => <a key={l.url} href={l.url} target="_blank" rel="noreferrer">{l.label}<ArrowUpRight size={11}/></a>)}</div>;
}
