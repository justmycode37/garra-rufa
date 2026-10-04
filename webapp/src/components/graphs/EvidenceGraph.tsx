'use client';
import { Fragment, useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, Search } from 'lucide-react';
import type { GraphLink, GraphNode, GraphRegion } from '@/lib/force-canvas';
import { familyOf, human, KIND_FAMILIES, LEVEL_COLOR, LEVELS, NEGATIVE, OTHER_FAMILY, PAPER_COLOR, paperUrl, type CandidatePath, type Evidence, type EvidenceEdge, type EvidenceView } from '@/lib/graph-views';
import { BackToOverview, GraphMessage, loadView, useGraphRun } from './GraphsShell';
import { useForceCanvas, ZoomControls } from './useForceCanvas';
import styles from './Graphs.module.css';

type Edge = EvidenceEdge & { id: string; type: 'evidence' } | { id: string; type: 'reports'; from: string; to: string; relation: string }
  | { id: string; type: 'related'; from: string; to: string; relation: string; link: EvidenceView['paper_links'][number] };
type Selection = { node: string } | { paper: string } | { edge: string } | null;
const pid = (k: string) => `paper:${k}`;

export default function EvidenceGraph() {
  const { run, loading } = useGraphRun();
  const [data, setData] = useState<EvidenceView>();
  const [error, setError] = useState('');
  useEffect(() => {
    setData(undefined); setError('');
    if (!run?.evidenceUrl) return;
    let live = true;
    loadView<EvidenceView>(run.evidenceUrl).then(d => live && setData(d)).catch(e => live && setError(e.message));
    return () => { live = false; };
  }, [run?.evidenceUrl]);
  if (loading) return <GraphMessage busy>Loading graphs…</GraphMessage>;
  if (!run) return <GraphMessage>No graphs yet. Build one from a disease, gene, or symptom query.</GraphMessage>;
  if (!run.evidenceUrl) {
    const building = run.build?.evidence && (run.build.state === 'running' || run.build.state === 'queued');
    return <GraphMessage busy={building}>{building ? `The evidence graph of ${run.label} is being built: papers are searched and read, which can take a while.`
      : `There is no evidence graph for ${run.label}. Build the graph again with “Also build the evidence graph” to read the literature.`}<BackToOverview/></GraphMessage>;
  }
  if (error) return <GraphMessage>{error}</GraphMessage>;
  if (!data) return <GraphMessage busy>Loading the evidence graph of {run.label}…</GraphMessage>;
  return <EvidenceExplorer key={run.id} data={data}/>;
}

function EvidenceExplorer({ data }: { data: EvidenceView }) {
  const model = useMemo(() => {
    // kinds in family order, so the legend and the territories read the same way round
    const families = [...KIND_FAMILIES, OTHER_FAMILY];
    const rank = (k: string) => { const f = familyOf(k); return families.indexOf(f) * 100 + (f.kinds.includes(k) ? f.kinds.indexOf(k) : 50); };
    const kinds = [...new Set(data.nodes.map(n => n.kind))].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    const otherKinds = kinds.filter(k => familyOf(k) === OTHER_FAMILY);
    const byId = Object.fromEntries(data.nodes.map(n => [n.id, n]));
    const paperBy = Object.fromEntries(data.papers.map(p => [p.key, p]));
    const candBy = Object.fromEntries(data.candidates.map(c => [c.id, c]));
    const levels = LEVELS.filter(l => data.edges.some(e => e.level === l));
    const edges: Edge[] = data.edges.map((e, i) => ({ ...e, id: `e${i}`, type: 'evidence' as const }));
    for (const n of data.nodes) if (n.id !== data.start) for (const k of n.papers) if (paperBy[k]) edges.push({ id: `r${edges.length}`, type: 'reports', from: pid(k), to: n.id, relation: 'reports' });
    for (const l of data.paper_links) if (l.shared_edges > 0) edges.push({ id: `l${edges.length}`, type: 'related', from: pid(l.a), to: pid(l.b), relation: 'shares evidence', link: l });
    const edgeBy = Object.fromEntries(edges.map(e => [e.id, e]));
    const degree: Record<string, number> = {};
    for (const e of edges) if (e.type === 'evidence') { degree[e.from] = (degree[e.from] ?? 0) + 1; degree[e.to] = (degree[e.to] ?? 0) + 1; }
    const related: Record<string, (EvidenceView['paper_links'][number] & { key: string })[]> = {};
    for (const l of data.paper_links) { (related[l.a] ||= []).push({ ...l, key: l.b }); (related[l.b] ||= []).push({ ...l, key: l.a }); }
    const color = (k: string) => {
      if (k === 'paper') return PAPER_COLOR;
      const f = familyOf(k), i = f === OTHER_FAMILY ? otherKinds.indexOf(k) : f.kinds.indexOf(k);
      return f.shades[i % f.shades.length];
    };
    return { kinds, byId, paperBy, candBy, levels, edges, edgeBy, degree, related, color };
  }, [data]);
  const { kinds, byId, paperBy, candBy, levels, edges, edgeBy, degree, related, color } = model;

  const [kindOn, setKindOn] = useState(() => new Set(kinds));
  const [levelOn, setLevelOn] = useState(() => new Set(levels));
  const [minConf, setMinConf] = useState(0.75);
  const [papers, setPapers] = useState(false);
  const [paperLinks, setPaperLinks] = useState(false);
  const [edgeLabels, setEdgeLabels] = useState(false);
  const [onlyNeighbours, setOnlyNeighbours] = useState(false);
  const [selection, setSelection] = useState<Selection>(null);
  const [cat, setCat] = useState<'direct' | 'transfer' | 'gaps'>('direct');
  const [query, setQuery] = useState('');
  const selectedNode = selection && 'node' in selection ? selection.node : selection && 'paper' in selection ? pid(selection.paper) : null;

  const neighbourOf = onlyNeighbours ? selectedNode : null;
  const visible = useMemo(() => {
    const show = (e: Edge) => e.type === 'reports' ? papers : e.type === 'related' ? paperLinks
      : levelOn.has(e.level) && (e.confidence || 0) >= minConf && kindOn.has(byId[e.from]?.kind) && kindOn.has(byId[e.to]?.kind);
    let es = edges.filter(show);
    const nodes = new Set([data.start]);
    if (neighbourOf) { es = es.filter(e => e.from === neighbourOf || e.to === neighbourOf); nodes.add(neighbourOf); }
    for (const e of es) { nodes.add(e.from); nodes.add(e.to); }
    return { edges: es, nodes };
  }, [edges, papers, paperLinks, levelOn, minConf, kindOn, byId, neighbourOf, data.start]);

  const { canvas, engine } = useForceCanvas({
    chargeStrength: -110, collidePadding: 3, labelZoom: 2.2, linkHits: true, insets: { left: 350, right: 380 },
    showLinkLabels: () => edgeLabels,
    onClick: hit => {
      if (hit?.node) setSelection(hit.node.id.startsWith('paper:') ? { paper: hit.node.id.slice(6) } : { node: hit.node.id });
      else if (hit?.link) { const e = edgeBy[hit.link.id]; setSelection(e && e.type !== 'evidence' ? { paper: e.from.slice(6) } : { edge: hit.link.id }); }
      else setSelection(null);
    },
  });

  useEffect(() => {
    if (!engine) return;
    const nodes: GraphNode[] = [];
    // every family gets a sector around the disease, as wide as its share of the visible
    // entities; within it each kind has its own heading, so like sits next to like
    const shown = [...visible.nodes].map(id => byId[id]).filter(n => n && n.id !== data.start);
    const perFamily = new Map<string, number>();
    for (const n of shown) perFamily.set(familyOf(n.kind).id, (perFamily.get(familyOf(n.kind).id) ?? 0) + 1);
    const present = [...KIND_FAMILIES, OTHER_FAMILY].filter(f => perFamily.has(f.id));
    const weight = (f: typeof OTHER_FAMILY) => 0.6 + Math.sqrt(perFamily.get(f.id)!);
    const total = present.reduce((s, f) => s + weight(f), 0);
    const ring = 110 + 22 * Math.sqrt(shown.length);
    const anchor: Record<string, { x: number; y: number }> = {};
    const regions: Record<string, GraphRegion> = {};
    let at = -Math.PI / 2;
    for (const f of present) {
      const span = 2 * Math.PI * weight(f) / total;
      const ks = kinds.filter(k => familyOf(k) === f && shown.some(n => n.kind === k));
      ks.forEach((k, i) => {
        const a = at + span * (0.18 + 0.64 * (ks.length > 1 ? i / (ks.length - 1) : 0.5));
        anchor[k] = { x: Math.cos(a) * ring, y: Math.sin(a) * ring };
      });
      regions[f.id] = { label: f.label, color: f.color };
      at += span;
    }
    for (const id of visible.nodes) {
      if (id.startsWith('paper:')) {
        const p = paperBy[id.slice(6)];
        if (p) nodes.push({ id, label: p.key, shape: 'square', r: 5, fill: PAPER_COLOR, priority: 0.2 });
        continue;
      }
      const n = byId[id];
      if (!n) continue;
      const start = id === data.start, cand = candBy[id];
      const home = anchor[n.kind];
      nodes.push({ id, label: n.label, r: start ? 24 : 4 + Math.sqrt(degree[id] ?? 1) * 2.1,
        fill: start ? '#141314' : color(n.kind), stroke: start ? '#fffdfe' : cand ? '#141314' : '#fffdfe', strokeWidth: start ? 4 : cand ? 1.8 : 1,
        pinLabel: start, fixed: start, x: start ? 0 : undefined, y: start ? 0 : undefined, fx: start ? 0 : undefined, fy: start ? 0 : undefined,
        group: start ? undefined : familyOf(n.kind).id, tx: home?.x, ty: home?.y,
        priority: start ? 10 : cand ? 0.6 + cand.score : Math.min(0.8, (degree[id] ?? 1) / 12), charge: start ? -800 : undefined, pull: start ? undefined : 0.06 });
    }
    const pairs = new Map<string, number>();
    const links: GraphLink[] = visible.edges.map(e => {
      const key = e.from < e.to ? `${e.from}|${e.to}` : `${e.to}|${e.from}`;
      const k = pairs.get(key) ?? 0; pairs.set(key, k + 1);
      const curve = k ? (k % 2 ? 1 : -1) * Math.ceil(k / 2) * 0.12 : 0;
      if (e.type === 'reports') return { id: e.id, source: e.from, target: e.to, color: LEVEL_COLOR.reports, width: 0.6, dash: [2, 3], opacity: 0.7, distance: 50, strength: 0.05, curve };
      if (e.type === 'related') return { id: e.id, source: e.from, target: e.to, color: LEVEL_COLOR.related, width: Math.min(6, e.link.shared_edges), opacity: 0.6, distance: 80, strength: 0.1, curve };
      const c = e.mostly_negative ? NEGATIVE : LEVEL_COLOR[e.level] ?? LEVEL_COLOR.review;
      // links across families are long and soft, so they bridge territories without dissolving them
      const across = familyOf(byId[e.from]?.kind ?? '') !== familyOf(byId[e.to]?.kind ?? '');
      return { id: e.id, source: e.from, target: e.to, color: c, width: 0.6 + 2.6 * (e.confidence || 0), opacity: 0.7, arrow: true, curve: curve || (across ? 0.08 : 0),
        dash: e.level === 'inferred' || e.mostly_negative ? [6, 4] : undefined, label: human(e.relation), distance: across ? 120 : 55, strength: across ? 0.08 : 0.3 };
    });
    engine.setGraph(nodes, links, { energy: 0.45, regions });
  }, [engine, visible, byId, paperBy, candBy, degree, color, data.start]);
  useEffect(() => { engine?.fitWhenSettled(); }, [engine]);
  useEffect(() => { engine?.setSelected(selectedNode); }, [engine, selectedNode]);
  useEffect(() => { engine?.reheat(0.02); }, [engine, edgeLabels]);

  function select(id: string) {
    setSelection(id.startsWith('paper:') ? { paper: id.slice(6) } : { node: id });
    if (engine?.has(id)) engine.focus(id, 1.8);
  }
  const nodeName = (id: string) => (id.startsWith('paper:') ? id.slice(6) : byId[id]?.label ?? id);
  const q = query.trim().toLowerCase();
  const hits = q ? [
    ...data.nodes.filter(n => n.label.toLowerCase().includes(q) || n.id.toLowerCase().includes(q) || n.names.some(x => x.toLowerCase().includes(q))).slice(0, 20).map(n => ({ id: n.id, label: n.label, sub: human(n.kind), color: color(n.kind) })),
    ...data.papers.filter(p => p.key.toLowerCase().includes(q) || (p.title || '').toLowerCase().includes(q)).slice(0, 8).map(p => ({ id: pid(p.key), label: p.title || p.key, sub: p.key, color: PAPER_COLOR })),
  ] : [];
  const nEvidence = visible.edges.filter(e => e.type === 'evidence').length;
  const nNodes = data.nodes.filter(n => visible.nodes.has(n.id)).length;
  const ctx = { data, byId, paperBy, candBy, edges, related, color, nodeName, select, setSelection };

  return <div className={styles.stage}>
    <canvas ref={canvas} className={styles.canvas} role="img" aria-label={`Evidence graph of existing solutions for ${data.disease}: ${nNodes} entities and ${nEvidence} evidence edges shown. The panels list candidates and quotes.`}/>
    <ZoomControls engine={engine}/>
    <aside className={`${styles.panel} ${styles.left}`} aria-label="Candidates and filters">
      <span className="eyebrow">EVIDENCE GRAPH</span>
      <h1 className={styles.panelTitle}>Existing solutions for {data.disease}</h1>
      <p className="fineprint">{data.settings.model && `${data.settings.model} · `}screened {data.stats.screened ?? '?'} · read {data.stats.read ?? data.papers.length} papers<br/>{nNodes}/{data.nodes.length} entities · {nEvidence}/{data.edges.length} edges shown · {data.candidates.length} candidates</p>
      <label className={styles.search}><Search size={14}/><input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Entity, paper title or ID…" aria-label="Search entities and papers"/></label>
      {hits.length > 0 && <div className={styles.list}>{hits.map(h => <button key={h.id} onClick={() => select(h.id)}><span className={styles.swatch} style={{ background: h.color }}/><span>{h.label}<small>{h.sub}</small></span></button>)}</div>}
      <h2 className={styles.h2}>Candidate solutions</h2>
      <div className={styles.segmented} role="tablist">{(['direct', 'transfer', 'gaps'] as const).map(c => <button key={c} role="tab" aria-selected={cat === c} onClick={() => setCat(c)}>{c === 'direct' ? 'Direct' : c === 'transfer' ? 'Transfer' : 'Gaps'}</button>)}</div>
      <Candidates cat={cat} {...ctx}/>
      <h2 className={styles.h2}>Entity kind</h2>
      <div className={styles.toggles}>{kinds.map((k, i) => <Fragment key={k}>
        {(i === 0 || familyOf(k) !== familyOf(kinds[i - 1])) && <span className={styles.family}><span className={styles.familyBand} style={{ background: familyOf(k).color }}/>{familyOf(k).label}</span>}
        <label className={styles.toggle}>
          <input type="checkbox" checked={kindOn.has(k)} onChange={e => setKindOn(s => { const n = new Set(s); if (e.target.checked) n.add(k); else n.delete(k); return n; })}/>
          <span className={styles.dot} style={{ background: color(k) }}/>{human(k)}<span className={styles.count}>{data.nodes.filter(n => n.kind === k && visible.nodes.has(n.id)).length}/{data.nodes.filter(n => n.kind === k).length}</span>
        </label>
      </Fragment>)}</div>
      <h2 className={styles.h2}>Evidence level</h2>
      <div className={styles.toggles}>{levels.map(l => <label key={l} className={styles.toggle}>
        <input type="checkbox" checked={levelOn.has(l)} onChange={e => setLevelOn(s => { const n = new Set(s); if (e.target.checked) n.add(l); else n.delete(l); return n; })}/>
        <span className={styles.bar} style={{ background: LEVEL_COLOR[l] }}/>{human(l)}<span className={styles.count}>{data.edges.filter(e => e.level === l).length}</span>
      </label>)}</div>
      <label className={styles.range}>Minimum edge confidence <b>{minConf.toFixed(2)}</b><input type="range" min={0} max={1} step={0.05} value={minConf} onChange={e => setMinConf(+e.target.value)}/></label>
      <h2 className={styles.h2}>Show</h2>
      <label className={styles.toggle}><input type="checkbox" checked={papers} onChange={e => setPapers(e.target.checked)}/>Papers as nodes</label>
      <label className={styles.toggle}><input type="checkbox" checked={paperLinks} onChange={e => setPaperLinks(e.target.checked)}/>Related-paper links</label>
      <label className={styles.toggle}><input type="checkbox" checked={edgeLabels} onChange={e => setEdgeLabels(e.target.checked)}/>Edge labels</label>
      <label className={styles.toggle}><input type="checkbox" checked={onlyNeighbours} onChange={e => setOnlyNeighbours(e.target.checked)}/>Only the selection’s neighbourhood</label>
    </aside>
    <aside className={`${styles.panel} ${styles.right}`} aria-label="Selection details" aria-live="polite">
      {selection && 'node' in selection && byId[selection.node] ? <NodeDetail id={selection.node} {...ctx}/>
        : selection && 'paper' in selection && paperBy[selection.paper] ? <PaperDetail paperKey={selection.paper} {...ctx}/>
        : selection && 'edge' in selection && edgeBy[selection.edge]?.type === 'evidence' ? <EdgeDetail edge={edgeBy[selection.edge] as EvidenceEdge & { id: string }} {...ctx}/>
        : <p className="muted">Click a node, an edge or a candidate. Edges carry the verbatim quotes that support them.</p>}
    </aside>
  </div>;
}

type Ctx = {
  data: EvidenceView; byId: Record<string, EvidenceView['nodes'][number]>; paperBy: Record<string, EvidenceView['papers'][number]>;
  candBy: Record<string, EvidenceView['candidates'][number]>; edges: Edge[]; related: Record<string, (EvidenceView['paper_links'][number] & { key: string })[]>;
  color: (k: string) => string; nodeName: (id: string) => string; select: (id: string) => void; setSelection: (s: Selection) => void;
};

function Candidates({ cat, data, color, select }: { cat: 'direct' | 'transfer' | 'gaps' } & Ctx) {
  if (cat === 'gaps') {
    const gs = (data.gaps || []).filter(g => g.status !== 'tested');
    if (!gs.length) return <p className="muted">No open research gaps.</p>;
    return <div className={styles.list}>{gs.slice(0, 60).map((g, i) => <button key={i} onClick={() => select(g.intervention)} title={[g.rationale, g.caveat, g.experiment].filter(Boolean).join('\n')}>
      <span className={styles.rank}>{i + 1}</span><span>{g.intervention_label} → {g.mechanism_label}<small>{human(g.status)} · {g.literature.count ?? '?'} papers · {g.trials.length} trials{g.plausibility != null ? ` · plausibility ${g.plausibility}/3` : ''}</small></span>
    </button>)}</div>;
  }
  const cs = data.candidates.filter(c => c.category === cat);
  if (!cs.length) return <p className="muted">None.</p>;
  return <div className={styles.list}>{cs.slice(0, 60).map((c, i) => <button key={c.id} onClick={() => select(c.id)}>
    <span className={styles.swatch} style={{ background: color(c.kind) }}/>
    <span>{i + 1}. {c.label}{c.tested_without_benefit_in_input && <b className={styles.neg} title="tested without benefit in the input disease"> ✕</b>}
      <small>{c.n_papers != null ? `${c.n_papers} paper${c.n_papers === 1 ? '' : 's'} · ${c.best_level ? human(c.best_level) : ''}` : human(c.kind)}{c.generic ? ' · generic' : ''}</small></span>
    <span className={styles.score}>{c.score.toFixed(2)}</span>
  </button>)}{cs.length > 60 && <p className="fineprint">… {cs.length - 60} more</p>}</div>;
}

function PaperRef({ k, paperBy, select }: { k: string } & Pick<Ctx, 'paperBy' | 'select'>) {
  const url = paperUrl(paperBy[k]);
  return <span className={styles.paperRef}><button onClick={() => select(pid(k))}>{k}</button>{url && <a href={url} target="_blank" rel="noreferrer" aria-label={`Open ${k}`}><ArrowUpRight size={11}/></a>}</span>;
}

function Quote({ ev, paperBy, select }: { ev: Evidence } & Pick<Ctx, 'paperBy' | 'select'>) {
  const neg = ev.effect === 'negative' || ev.effect === 'null';
  return <blockquote className={styles.quote}>“{ev.quote}”
    <small><PaperRef k={ev.paper} paperBy={paperBy} select={select}/> {ev.passage} · <span className={neg ? styles.neg : undefined}>{[ev.level && human(ev.level), ev.effect !== 'na' && ev.effect, ev.organism, ev.section].filter(Boolean).join(' · ')}</span></small>
  </blockquote>;
}

function PathRow({ x }: { x: CandidatePath }) {
  return <details className={styles.details}><summary><b>{x.edge}</b><small>score {x.score} · {human(x.level)} · conf {x.confidence} · {x.papers} paper(s){x.negative_papers ? <span className={styles.neg}> · {x.negative_papers} negative/null</span> : null}<br/>bridge: {x.bridge} (×{x.bridge_weight})</small></summary>
    {x.evidence.map((q, i) => <blockquote key={i} className={styles.quote}>{q}</blockquote>)}
    {x.bridge_evidence.map((q, i) => <blockquote key={`b${i}`} className={styles.quote}><small>bridge:</small> {q}</blockquote>)}
  </details>;
}

function EdgeRow({ e, from, ...ctx }: { e: EvidenceEdge; from: string } & Ctx) {
  const out = e.from === from, other = out ? e.to : e.from;
  const head = <><span className={styles.rel}>{out ? '→' : '←'} {human(e.relation)}</span> <button className={styles.inlineLink} onClick={ev => { ev.preventDefault(); ctx.select(other); }}>{ctx.nodeName(other)}</button>
    <small>{human(e.level)} · conf {e.confidence} · {e.papers} paper(s){e.negative_papers ? <span className={styles.neg}> · {e.negative_papers} negative/null</span> : null}</small></>;
  if (!e.evidence?.length) return <div className={styles.details}>{head}</div>;
  return <details className={styles.details}><summary>{head}</summary>{e.evidence.map((ev, i) => <Quote key={i} ev={ev} {...ctx}/>)}</details>;
}

function NodeDetail({ id, ...ctx }: { id: string } & Ctx) {
  const { byId, candBy, edges, paperBy, color, select } = ctx;
  const n = byId[id], c = candBy[id];
  const es = edges.filter((e): e is EvidenceEdge & { id: string; type: 'evidence' } => e.type === 'evidence' && (e.from === id || e.to === id)).sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
  return <>
    <span className="eyebrow"><span className={styles.dot} style={{ background: color(n.kind) }}/> {human(n.kind).toUpperCase()}</span>
    <h2 className={styles.detailTitle}>{n.label}</h2>
    <dl className={styles.facts}>
      <div><dt>ID</dt><dd><code>{n.id}</code></dd></div>
      <div><dt>Mapped by</dt><dd>{n.how}{n.in_source_graph ? ' · in source graph' : ''}</dd></div>
      {n.names.length > 0 && <div><dt>Names in papers</dt><dd>{n.names.map(x => <span key={x} className={styles.tag}>{x}</span>)}</dd></div>}
      {n.xrefs.length > 0 && <div><dt>Xrefs</dt><dd>{n.xrefs.map(x => <code key={x}>{x} </code>)}</dd></div>}
    </dl>
    {c && <>
      <h3 className={styles.h2}>Candidate solution · {c.category} · score {c.score}</h3>
      {c.n_papers != null && <p className="fineprint">{c.n_papers} supporting paper(s) · best evidence {c.best_level ? human(c.best_level) : 'none'} · tier {c.tier}/3{c.n_papers === 1 && <span className={styles.neg}> · single paper</span>}{c.generic && ' · generic technique (down-weighted)'}{c.other_disease_endpoint && ' · endpoint of another disease (down-weighted)'}</p>}
      {c.tested_without_benefit_in_input && <p className={styles.neg}>Tested without benefit in the input disease.</p>}
      {c.paths.map((x, i) => <PathRow key={i} x={x}/>)}
    </>}
    <h3 className={styles.h2}>Evidence edges ({es.length})</h3>
    {es.length ? es.map(e => <EdgeRow key={e.id} e={e} from={id} {...ctx}/>) : <p className="muted">none</p>}
    <h3 className={styles.h2}>Papers ({n.papers.length})</h3>
    {n.papers.map(k => <p key={k} className={styles.paperLine}><PaperRef k={k} paperBy={paperBy} select={select}/> <small>{paperBy[k]?.title}</small></p>)}
  </>;
}

function PaperDetail({ paperKey, ...ctx }: { paperKey: string } & Ctx) {
  const { data, paperBy, edges, related, nodeName, select } = ctx;
  const p = paperBy[paperKey];
  const ents = data.nodes.filter(n => n.papers.includes(paperKey) && n.id !== data.start);
  const es = edges.filter((e): e is EvidenceEdge & { id: string; type: 'evidence' } => e.type === 'evidence' && e.evidence.some(v => v.paper === paperKey));
  const rel = (related[paperKey] || []).slice().sort((a, b) => b.shared_edges - a.shared_edges || b.shared_entities - a.shared_entities);
  const links = [p.pmid && ['PubMed', `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(p.pmid)}/`], p.pmcid && ['PMC', `https://www.ncbi.nlm.nih.gov/pmc/articles/${encodeURIComponent(p.pmcid)}/`], p.doi && ['DOI', `https://doi.org/${p.doi}`]].filter(Boolean) as [string, string][];
  return <>
    <span className="eyebrow">PAPER · {p.key}</span>
    <h2 className={styles.detailTitle}>{p.title || p.key}</h2>
    <p className="fineprint">{p.journal} {p.year} · text: {p.text_source}{p.cited_by != null ? ` · cited by ${p.cited_by}` : ''}{p.origin === 'transfer' ? ' · transfer search' : ''}</p>
    {links.length > 0 && <div className={styles.links}>{links.map(([l, u]) => <a key={l} href={u} target="_blank" rel="noreferrer">{l}<ArrowUpRight size={11}/></a>)}</div>}
    {p.summary && <p className={styles.body}>{p.summary}</p>}
    <h3 className={styles.h2}>Related papers ({rel.length})</h3>
    {rel.length ? rel.map(r => <p key={r.key} className={styles.paperLine}><PaperRef k={r.key} paperBy={paperBy} select={select}/> <small>{r.shared_edges} shared edges · {r.shared_entities} shared entities<br/>{paperBy[r.key]?.title}</small></p>) : <p className="muted">none</p>}
    <h3 className={styles.h2}>Edges it supports ({es.length})</h3>
    {es.map(e => <details key={e.id} className={styles.details}><summary><button className={styles.inlineLink} onClick={ev => { ev.preventDefault(); select(e.from); }}>{nodeName(e.from)}</button> <span className={styles.rel}>{human(e.relation)}</span> <button className={styles.inlineLink} onClick={ev => { ev.preventDefault(); select(e.to); }}>{nodeName(e.to)}</button></summary>
      {e.evidence.filter(v => v.paper === paperKey).map((ev, i) => <Quote key={i} ev={ev} {...ctx}/>)}</details>)}
    <h3 className={styles.h2}>Entities ({ents.length})</h3>
    <div>{ents.map(n => <button key={n.id} className={styles.tag} onClick={() => select(n.id)}>{n.label}</button>)}</div>
  </>;
}

function EdgeDetail({ edge: e, ...ctx }: { edge: EvidenceEdge & { id: string } } & Ctx) {
  const { candBy, nodeName, select } = ctx;
  const c = e.level === 'inferred' ? candBy[e.from] : null;
  return <>
    <span className="eyebrow" style={{ color: e.mostly_negative ? NEGATIVE : LEVEL_COLOR[e.level] }}>{human(e.level).toUpperCase()} EVIDENCE</span>
    <h2 className={styles.detailTitle}><button className={styles.inlineLink} onClick={() => select(e.from)}>{nodeName(e.from)}</button> <span className={styles.rel}>{human(e.relation)}</span> <button className={styles.inlineLink} onClick={() => select(e.to)}>{nodeName(e.to)}</button></h2>
    <p className="fineprint">confidence {e.confidence} · {e.papers} paper(s){e.negative_papers ? <span className={styles.neg}> · {e.negative_papers} negative/null</span> : null}</p>
    {c ? <><h3 className={styles.h2}>Inferred from</h3>{c.paths.map((x, i) => <PathRow key={i} x={x}/>)}</>
      : <><h3 className={styles.h2}>Evidence ({e.evidence.length})</h3>{e.evidence.map((ev, i) => <Fragment key={i}><Quote ev={ev} {...ctx}/></Fragment>)}</>}
  </>;
}
