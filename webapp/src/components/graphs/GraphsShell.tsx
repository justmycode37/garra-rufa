'use client';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ArrowLeft, ChevronDown, LoaderCircle, Plus, Sparkles, X } from 'lucide-react';
import { GarraMark } from '@/components/Brand';
import type { GraphBuild } from '@/lib/graph-builds';
import { CACHE_KEY, cachedExample, forget, isBuildId, readCache, remember, type ExampleEntry } from '@/lib/graph-cache';
import GraphChat from './GraphChat';
import styles from './Graphs.module.css';

export type GraphRun = {
  id: string; label: string; source: 'example' | 'built';
  presentUrl?: string; evidenceUrl?: string; build?: GraphBuild;
};
type Caps = { enabled: boolean; evidence: boolean; notice?: string };
/** startGraph opens the cached graph of a query or starts its build, and selects it. */
type Ctx = {
  runs: GraphRun[]; run?: GraphRun; loading: boolean; error: string;
  canBuild: { signedIn: boolean | null; enabled: boolean; evidence: boolean; notice?: string };
  startGraph: (query: string, evidence?: boolean) => Promise<{ error?: string; cached?: boolean }>;
  /** Adds the evidence graph to a built graph; resolves to an error message or ''. */
  addEvidence: (id: string) => Promise<string>;
};

const GraphsContext = createContext<Ctx>({ runs: [], loading: true, error: '', canBuild: { signedIn: null, enabled: false, evidence: false },
  startGraph: async () => ({ error: 'Graphs are not loaded yet.' }), addEvidence: async () => 'Graphs are not loaded yet.' });
export const useGraphRun = () => useContext(GraphsContext);

const viewCache = new Map<string, Promise<unknown>>();
/** Fetch a view JSON once per URL (built graphs carry their update time in the URL). */
export function loadView<T>(url: string): Promise<T> {
  if (!viewCache.has(url)) {
    if (viewCache.size > 8) viewCache.delete(viewCache.keys().next().value!);
    const p = fetch(url).then(async r => {
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'This graph could not be loaded.');
      return r.json();
    });
    p.catch(() => viewCache.delete(url));
    viewCache.set(url, p);
  }
  return viewCache.get(url) as Promise<T>;
}

const STAGE_LABEL: Record<string, string> = {
  graph: 'Collecting the knowledge graph', overview: 'Preparing the overview', papers: 'Searching the literature',
  evidence: 'Reading papers for evidence', export: 'Preparing the evidence graph',
};

function storeCache(ids: string[]) {
  try { localStorage.setItem(CACHE_KEY, JSON.stringify(ids)); } catch { /* the page works without it */ }
}

export default function GraphsShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [examples, setExamples] = useState<ExampleEntry[]>([]);
  const [examplesLoaded, setExamplesLoaded] = useState(false);
  const [caps, setCaps] = useState<Caps>({ enabled: false, evidence: false });
  // the graphs this browser has queried (most recent first) and their builds by id
  const [cache, setCache] = useState<string[]>([]);
  const [builds, setBuilds] = useState<Record<string, GraphBuild>>({});
  const [resolved, setResolved] = useState<Set<string>>(() => new Set());
  const [allBuilds, setAllBuilds] = useState<string[]>([]);
  const [runId, setRunId] = useState('');
  const [error, setError] = useState('');
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [builderOpen, setBuilderOpen] = useState(false);
  const requested = useRef(new Set<string>());

  const updateCache = useCallback((change: (ids: string[]) => string[]) => setCache(ids => {
    const next = change(ids);
    if (next !== ids) storeCache(next);
    return next;
  }), []);

  const fetchBuild = useCallback(async (id: string) => {
    try {
      const r = await fetch(`/api/graphs/${encodeURIComponent(id)}`);
      if (r.status === 404) updateCache(ids => forget(ids, id));  // gone from the research service
      else if (r.ok) { const { build } = await r.json(); setBuilds(b => ({ ...b, [id]: build })); }
    } catch { /* tried again on the next poll or visit */ }
    finally { setResolved(s => s.has(id) ? s : new Set(s).add(id)); }
  }, [updateCache]);

  useEffect(() => {
    setRunId(new URLSearchParams(window.location.search).get('run') || '');
    let stored: string | null = null;
    try { stored = localStorage.getItem(CACHE_KEY); } catch { /* no cache */ }
    setCache(readCache(stored));
    fetch('/api/graphs').then(r => r.json()).then(d => {
      setCaps({ enabled: !!d.enabled, evidence: !!d.evidence, notice: d.notice });
      // every graph built so far is selectable: no need to fetch these one by one
      const listed: GraphBuild[] = Array.isArray(d.builds) ? d.builds : [];
      listed.forEach(b => requested.current.add(b.id));
      setBuilds(s => ({ ...Object.fromEntries(listed.map(b => [b.id, b])), ...s }));
      setResolved(s => new Set([...s, ...listed.map(b => b.id)]));
      setAllBuilds(listed.map(b => b.id));
    }).catch(() => setCaps({ enabled: false, evidence: false }));
    fetch('/api/auth').then(r => r.json()).then(d => setSignedIn(!!d.user && !d.user.guest)).catch(() => setSignedIn(false));
    fetch('/graph-data/index.json').then(r => (r.ok ? r.json() : []))
      .then(index => setExamples(Array.isArray(index) ? index : []))
      .catch(() => setError('Graphs could not be loaded.'))
      .finally(() => setExamplesLoaded(true));
  }, []);

  // load the builds of the cached graphs (and of ?run=) once each
  useEffect(() => {
    for (const id of [runId, ...cache]) if (id && isBuildId(id) && !requested.current.has(id)) { requested.current.add(id); void fetchBuild(id); }
  }, [runId, cache, fetchBuild]);

  // poll the builds that are still running
  const active = Object.values(builds).filter(b => b.state === 'queued' || b.state === 'running').map(b => b.id).join(' ');
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => active.split(' ').forEach(id => void fetchBuild(id)), 4000);
    return () => clearInterval(timer);
  }, [active, fetchBuild]);

  const runs = useMemo<GraphRun[]>(() => {
    const exampleBy = new Map(examples.map(e => [e.id, e]));
    // the graphs this browser queried first, then every other built graph and example
    return [...new Set([runId, ...cache, ...allBuilds, ...examples.map(e => e.id)])].flatMap<GraphRun>(id => {
      const b = builds[id], e = exampleBy.get(id);
      if (b) return [{ id, label: b.label, source: 'built', build: b,
        presentUrl: b.views.present ? `/api/graphs/${b.id}/present?v=${b.done.length}` : undefined,
        evidenceUrl: b.views.evidence ? `/api/graphs/${b.id}/evidence?v=${b.done.length}` : undefined }];
      if (e) return [{ id, label: e.label, source: 'example',
        presentUrl: e.present ? `/graph-data/${e.present}` : undefined,
        evidenceUrl: e.evidence ? `/graph-data/${e.evidence}` : undefined }];
      return [];
    });
  }, [builds, examples, cache, allBuilds, runId]);

  // ?run=<id> (a build started from the atlas) waits for its build instead of showing another graph
  const waiting = !examplesLoaded || (!!runId && isBuildId(runId) && !runs.some(r => r.id === runId) && !resolved.has(runId));
  const run = runs.find(r => r.id === runId) ?? (waiting ? undefined : runs[0]);
  const select = useCallback((id: string) => {
    setRunId(id);
    const params = new URLSearchParams(window.location.search);
    params.set('run', id);
    window.history.replaceState(null, '', `${window.location.pathname}?${params}`);
  }, []);

  // a graph that opened counts as queried: it moves to the top of the cache
  useEffect(() => { if (run) updateCache(ids => ids[0] === run.id ? ids : remember(ids, run.id)); }, [run, updateCache]);
  // nothing queried yet: start with the query form
  useEffect(() => { if (!waiting && !runs.length) setBuilderOpen(true); }, [waiting, runs.length]);

  const open = (b: GraphBuild) => {
    setBuilds(s => ({ ...s, [b.id]: b }));
    requested.current.add(b.id);
    setResolved(s => new Set(s).add(b.id));
    updateCache(ids => remember(ids, b.id));
    select(b.id);
  };
  const startGraph: Ctx['startGraph'] = async (graphQuery, evidence = false) => {
    const example = cachedExample(examples, graphQuery, evidence);
    if (example) { updateCache(ids => remember(ids, example.id)); select(example.id); return { cached: true }; }
    try {
      const r = await fetch('/api/graphs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: graphQuery, evidence }) });
      const d = await r.json();
      if (!r.ok) return { error: d.error || 'The graph could not be started.' };
      open(d.build);
      return { cached: !!d.build.views.present && (!evidence || !!d.build.views.evidence) };
    } catch { return { error: 'The graph could not be started.' }; }
  };
  const addEvidence = async (id: string) => {
    try {
      const r = await fetch(`/api/graphs/${encodeURIComponent(id)}/evidence`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      const d = await r.json();
      if (!r.ok) return d.error || 'The evidence graph could not be started.';
      setBuilds(s => ({ ...s, [id]: d.build }));
      return '';
    } catch { return 'The evidence graph could not be started.'; }
  };
  const canBuild = { signedIn, ...caps };

  const tab = pathname?.endsWith('/evidence') ? 'evidence' : 'overview';
  const query = run ? `?run=${encodeURIComponent(run.id)}` : '';
  const status = (r: GraphRun) => !r.build || r.build.state === 'done' ? '' : r.build.state === 'failed' ? ' · failed' : ' · building…';

  return <GraphsContext.Provider value={{ runs, run, loading: waiting, error, canBuild, startGraph, addEvidence }}>
    <div className={styles.page}>
      <a className="skip-link" href="#graph-content">Skip to graph</a>
      <header className={styles.header}>
        <Link className={`brand ${styles.brand}`} href="/?view=explore" aria-label="Garra Rufa home"><GarraMark size={26}/><span>garra rufa<span className="brand-period">.</span></span></Link>
        <nav className={styles.nav} aria-label="Main navigation">
          <Link href="/?view=explore">Home</Link><Link href="/?view=atlas">Atlas</Link>
          <Link href="/graphs/overview" aria-current="page">Graphs</Link><Link href="/about">About</Link>
        </nav>
      </header>
      <div className={styles.toolbar}>
        <div className={styles.title}>
          <span className="eyebrow">KNOWLEDGE GRAPHS</span>
          {runs.length > 1 ? <label className={styles.runPicker}>
            <span className={styles.srOnly}>Your graphs</span>
            <select value={run?.id ?? ''} onChange={e => select(e.target.value)} aria-label="Your graphs">
              {runs.map(r => <option key={r.id} value={r.id}>{r.label}{status(r)}</option>)}
            </select>
            <ChevronDown size={16} aria-hidden="true"/>
          </label> : <h1 className={styles.runTitle}>{run?.label ?? (waiting ? '' : 'No graph yet')}</h1>}
        </div>
        <nav className={styles.tabs} aria-label="Graph views">
          <Link href={`/graphs/overview${query}`} aria-current={tab === 'overview' ? 'page' : undefined}>Disease overview</Link>
          <Link href={`/graphs/evidence${query}`} aria-current={tab === 'evidence' ? 'page' : undefined}>Evidence</Link>
        </nav>
        <button className={`primary ${styles.buildButton}`} onClick={() => setBuilderOpen(o => !o)} aria-expanded={builderOpen}><Plus size={15}/>Build a graph</button>
      </div>
      {builderOpen && <BuildPanel caps={caps} signedIn={signedIn} startGraph={startGraph} onClose={() => setBuilderOpen(false)}/>}
      {run?.build && run.build.state !== 'done' && <BuildProgress build={run.build}/>}
      <main id="graph-content" className={styles.main}>
        {error ? <p className={styles.notice} role="alert">{error}</p> : children}
      </main>
      {run && <GraphChat key={`${run.id}:${tab}`} run={run} view={tab === 'evidence' ? 'evidence' : 'present'}/>}
    </div>
  </GraphsContext.Provider>;
}

function BuildPanel({ caps, signedIn, startGraph, onClose }: { caps: Caps; signedIn: boolean | null; startGraph: Ctx['startGraph']; onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [evidence, setEvidence] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || query.trim().length < 2) return;
    setBusy(true); setMessage('');
    const { error, cached } = await startGraph(query.trim(), evidence);
    setBusy(false);
    if (error) { setMessage(error); return; }
    setQuery('');
    setMessage(cached ? 'This query was built before. Its graph opened from the cache.' : 'Building. The overview opens as soon as it is ready.');
  }
  return <section className={`${styles.builder} glass`} aria-labelledby="build-title">
    <div className={styles.builderHead}>
      <div><h2 id="build-title"><Sparkles size={17}/>Build a new graph</h2>
        <p className="muted">Enter a disease (“Marfan syndrome”), a gene (“FBN1”) or symptoms (“tall stature, ectopia lentis”). A query built before opens from the cache at once; a new one is collected from public rare-disease sources and takes a few minutes.</p></div>
      <button className={styles.iconButton} onClick={onClose} aria-label="Close graph builder"><X size={18}/></button>
    </div>
    {/* cached graphs open for everyone; a new build needs the service and a sign-in */}
    <form className={styles.buildForm} onSubmit={submit}>
        <input value={query} onChange={e => setQuery(e.target.value)} maxLength={120} placeholder="Disease, gene, or symptoms" aria-label="Graph query" disabled={busy}/>
        <label className={styles.check} title={caps.evidence ? undefined : 'Needs an LLM key on the research service'}>
          <input type="checkbox" checked={evidence} disabled={!caps.evidence || busy} onChange={e => setEvidence(e.target.checked)}/>
          Also build the evidence graph <span className="fineprint">(reads papers with an LLM; can take an hour; can also be added later from the Evidence tab)</span>
        </label>
        <button className="primary" disabled={busy || query.trim().length < 2}>{busy ? <LoaderCircle size={15} className={styles.spin}/> : <Plus size={15}/>}Build</button>
      </form>
    {!caps.enabled ? <p className={styles.notice}>{caps.notice || 'Building new graphs is unavailable right now.'} Graphs in the cache still open.</p>
      : signedIn === false && <p className={styles.notice}><Link href="/?entry=signup&role=researcher">Sign in</Link> to build new graphs. Graphs in the cache open without it.</p>}
    {message && <p className={styles.message} role="status">{message}</p>}
    <p className="fineprint">Built graphs are kept on the research service, so the same query is answered from the cache. Do not include personal or patient details in the query.</p>
  </section>;
}

function BuildProgress({ build }: { build: GraphBuild }) {
  return <div className={styles.progress} role="status">
    {build.state === 'failed' ? <X size={15}/> : <LoaderCircle size={15} className={styles.spin}/>}
    <span><b>{build.label}</b> · {build.state === 'failed' ? build.error : build.state === 'queued' ? 'waiting to start' : STAGE_LABEL[build.stage ?? ''] ?? 'building'}</span>
    <ol aria-label="Build stages">{build.stages.map(s => <li key={s} data-done={build.done.includes(s)} data-active={build.stage === s} title={STAGE_LABEL[s]}/>)}</ol>
  </div>;
}

export function GraphMessage({ children, busy }: { children: ReactNode; busy?: boolean }) {
  return <div className={styles.empty} role="status">{busy && <LoaderCircle size={18} className={styles.spin}/>}<div>{children}</div></div>;
}

export function BackToOverview() {
  const { run } = useGraphRun();
  return <Link className={styles.backLink} href={`/graphs/overview${run ? `?run=${encodeURIComponent(run.id)}` : ''}`}><ArrowLeft size={14}/>Disease overview</Link>;
}
