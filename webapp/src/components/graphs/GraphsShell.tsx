'use client';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ArrowLeft, Check, ChevronDown, LoaderCircle, Plus, Sparkles, X } from 'lucide-react';
import { GarraMark } from '@/components/Brand';
import type { GraphBuild } from '@/lib/graph-builds';
import { diseaseRequest, findDiseaseRun, type DiseaseRequest } from '@/lib/organ-diseases';
import styles from './Graphs.module.css';

export type GraphRun = {
  id: string; label: string; source: 'example' | 'built';
  presentUrl?: string; evidenceUrl?: string; build?: GraphBuild;
  ids?: string[]; names?: string[]; query?: string;
};
type IndexEntry = { id: string; label: string; present: string | null; evidence: string | null; ids?: string[]; names?: string[] };
/** A disease opened from the atlas (?disease=…&name=…) until its graph is selected. */
type DiseaseOpen = DiseaseRequest & { message?: string; busy?: boolean };
type BuildState = { enabled: boolean; evidence: boolean; notice?: string; builds: GraphBuild[] };
type Ctx = { runs: GraphRun[]; run?: GraphRun; loading: boolean; error: string };

const GraphsContext = createContext<Ctx>({ runs: [], loading: true, error: '' });
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

export default function GraphsShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [examples, setExamples] = useState<IndexEntry[]>([]);
  const [builds, setBuilds] = useState<BuildState>({ enabled: false, evidence: false, builds: [] });
  const [runId, setRunId] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [buildsLoaded, setBuildsLoaded] = useState(false);
  const [disease, setDisease] = useState<DiseaseOpen>();
  const pendingSelect = useRef('');
  const diseaseHandled = useRef(false);

  const refreshBuilds = useCallback(async () => {
    try {
      const r = await fetch('/api/graphs');
      const d = await r.json();
      if (r.ok) setBuilds(d);
    } catch { /* the examples still work */ }
    finally { setBuildsLoaded(true); }
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setRunId(params.get('run') || '');
    if (!params.get('run')) setDisease(diseaseRequest(params));
    // the examples show at once; builds and the sign-in state arrive on their own
    void refreshBuilds();
    fetch('/api/auth').then(r => r.json()).then(d => setSignedIn(!!d.user && !d.user.guest)).catch(() => setSignedIn(false));
    fetch('/graph-data/index.json').then(r => (r.ok ? r.json() : []))
      .then(index => setExamples(Array.isArray(index) ? index : []))
      .catch(() => setError('Graphs could not be loaded.'))
      .finally(() => setLoading(false));
  }, [refreshBuilds]);

  // poll while a build runs
  const active = builds.builds.some(b => b.state === 'queued' || b.state === 'running');
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(refreshBuilds, 4000);
    return () => clearInterval(timer);
  }, [active, refreshBuilds]);

  const runs = useMemo<GraphRun[]>(() => [
    ...builds.builds.filter(b => b.state !== 'failed' || b.views.present).map(b => ({
      id: b.id, label: b.label, source: 'built' as const, build: b,
      presentUrl: b.views.present ? `/api/graphs/${b.id}/present?v=${b.done.length}` : undefined, query: b.query,
      evidenceUrl: b.views.evidence ? `/api/graphs/${b.id}/evidence?v=${b.done.length}` : undefined,
    })),
    ...examples.map(e => ({
      id: e.id, label: e.label, source: 'example' as const, ids: e.ids, names: e.names,
      presentUrl: e.present ? `/graph-data/${e.present}` : undefined,
      evidenceUrl: e.evidence ? `/graph-data/${e.evidence}` : undefined,
    })),
  ], [builds.builds, examples]);

  // A disease from the atlas shows its own graph, never a fallback example.
  const run = runs.find(r => r.id === runId) ?? (disease ? undefined : runs.find(r => r.source === 'example') ?? runs[0]);
  const select = (id: string) => {
    setRunId(id); setDisease(undefined);
    const params = new URLSearchParams(window.location.search);
    params.set('run', id); params.delete('disease'); params.delete('name');
    window.history.replaceState(null, '', `${window.location.pathname}?${params}`);
  };

  // Open the atlas disease: its existing graph, else a new build of it (by identifier).
  useEffect(() => {
    if (!disease || diseaseHandled.current || loading || !buildsLoaded || signedIn === null) return;
    diseaseHandled.current = true;
    const found = findDiseaseRun(runs, disease);
    if (found) { select(found.id); return; }
    if (!builds.enabled) { setDisease({ ...disease, message: `There is no graph of ${disease.name} yet, and building graphs is unavailable right now. ${builds.notice ?? ''}` }); return; }
    if (!signedIn) { setDisease({ ...disease, message: `There is no graph of ${disease.name} yet. Sign in to build it.` }); return; }
    setDisease({ ...disease, busy: true, message: `Starting a graph of ${disease.name}…` });
    const id = disease.ids.find(i => i.startsWith('ORPHA:')) ?? disease.ids[0];
    fetch('/api/graphs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: id, label: disease.name }) })
      .then(async r => {
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || 'The graph could not be started.');
        setBuilds(s => ({ ...s, builds: [d.build, ...s.builds.filter(x => x.id !== d.build.id)] }));
        pendingSelect.current = d.build.id;
      })
      .catch(e => setDisease({ ...disease, message: `The graph of ${disease.name} could not be started: ${e instanceof Error ? e.message : 'please try again.'}` }));
  }, [disease, loading, buildsLoaded, signedIn, runs, builds.enabled, builds.notice]);
  useEffect(() => {
    if (pendingSelect.current && runs.some(r => r.id === pendingSelect.current)) { select(pendingSelect.current); pendingSelect.current = ''; }
  }, [runs]);

  const tab = pathname?.endsWith('/evidence') ? 'evidence' : 'overview';
  const query = run ? `?run=${encodeURIComponent(run.id)}` : '';
  const examplesList = runs.filter(r => r.source === 'example'), builtList = runs.filter(r => r.source === 'built');

  return <GraphsContext.Provider value={{ runs, run, loading, error }}>
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
          <label className={styles.runPicker}>
            <span className={styles.srOnly}>Disease graph</span>
            <select value={run?.id ?? ''} onChange={e => select(e.target.value)} disabled={!runs.length} aria-label="Disease graph">
              {builtList.length > 0 && <optgroup label="Built from queries">{builtList.map(r => <option key={r.id} value={r.id}>{r.label}{r.build && r.build.state !== 'done' ? ' · building…' : ''}</option>)}</optgroup>}
              {examplesList.length > 0 && <optgroup label="Examples">{examplesList.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}</optgroup>}
            </select>
            <ChevronDown size={16} aria-hidden="true"/>
          </label>
        </div>
        <nav className={styles.tabs} aria-label="Graph views">
          <Link href={`/graphs/overview${query}`} aria-current={tab === 'overview' ? 'page' : undefined}>Disease overview</Link>
          <Link href={`/graphs/evidence${query}`} aria-current={tab === 'evidence' ? 'page' : undefined}>Evidence</Link>
        </nav>
        <button className={`primary ${styles.buildButton}`} onClick={() => setBuilderOpen(o => !o)} aria-expanded={builderOpen}><Plus size={15}/>Build a graph</button>
      </div>
      {builderOpen && <BuildPanel builds={builds} signedIn={signedIn} onClose={() => setBuilderOpen(false)}
        onStarted={b => { setBuilds(s => ({ ...s, builds: [b, ...s.builds.filter(x => x.id !== b.id)] })); pendingSelect.current = b.id; }}
        onOpen={id => { select(id); setBuilderOpen(false); }}/>}
      {run?.build && run.build.state !== 'done' && <BuildProgress build={run.build}/>}
      <main id="graph-content" className={styles.main}>
        {error ? <p className={styles.notice} role="alert">{error}</p>
          : disease && !run ? <GraphMessage busy={!disease.message || disease.busy}>{disease.message ?? `Looking for the graph of ${disease.name}…`}
            {!disease.busy && disease.message && signedIn === false && <p><Link href="/?entry=signup&role=researcher">Sign in</Link> · <Link href="/?view=atlas">Back to the atlas</Link></p>}</GraphMessage>
          : children}
      </main>
    </div>
  </GraphsContext.Provider>;
}

function BuildPanel({ builds, signedIn, onClose, onStarted, onOpen }: { builds: BuildState; signedIn: boolean | null; onClose: () => void; onStarted: (b: GraphBuild) => void; onOpen: (id: string) => void }) {
  const [query, setQuery] = useState('');
  const [evidence, setEvidence] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || query.trim().length < 2) return;
    setBusy(true); setMessage('');
    try {
      const r = await fetch('/api/graphs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: query.trim(), evidence }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'The graph could not be started.');
      onStarted(d.build); setQuery('');
      setMessage(d.build.state === 'done' ? 'This graph already exists — it is now selected.' : 'Building. The overview opens as soon as it is ready.');
    } catch (e) { setMessage(e instanceof Error ? e.message : 'The graph could not be started.'); }
    finally { setBusy(false); }
  }
  const recent = builds.builds.slice(0, 6);
  return <section className={`${styles.builder} glass`} aria-labelledby="build-title">
    <div className={styles.builderHead}>
      <div><h2 id="build-title"><Sparkles size={17}/>Build a new graph</h2>
        <p className="muted">Enter a disease (“Marfan syndrome”), a gene (“FBN1”) or symptoms (“tall stature, ectopia lentis”). The overview is collected from public rare-disease sources and takes a few minutes.</p></div>
      <button className={styles.iconButton} onClick={onClose} aria-label="Close graph builder"><X size={18}/></button>
    </div>
    {!builds.enabled ? <p className={styles.notice}>{builds.notice || 'Building graphs is unavailable right now.'} The example graphs can still be explored.</p>
      : signedIn === false ? <p className={styles.notice}><Link href="/?entry=signup&role=researcher">Sign in</Link> to build a graph.</p>
      : <form className={styles.buildForm} onSubmit={submit}>
        <input value={query} onChange={e => setQuery(e.target.value)} maxLength={120} placeholder="Disease, gene, or symptoms" aria-label="Graph query" disabled={busy}/>
        <label className={styles.check} title={builds.evidence ? undefined : 'Needs an LLM key on the research service'}>
          <input type="checkbox" checked={evidence} disabled={!builds.evidence || busy} onChange={e => setEvidence(e.target.checked)}/>
          Also build the evidence graph <span className="fineprint">(reads papers with an LLM; can take an hour)</span>
        </label>
        <button className="primary" disabled={busy || query.trim().length < 2}>{busy ? <LoaderCircle size={15} className={styles.spin}/> : <Plus size={15}/>}Build</button>
      </form>}
    {message && <p className={styles.message} role="status">{message}</p>}
    <p className="fineprint">Graphs you build are listed for everyone using this site. Do not include personal or patient details in the query.</p>
    {recent.length > 0 && <ul className={styles.buildList}>{recent.map(b => <li key={b.id}>
      <button onClick={() => onOpen(b.id)} disabled={!b.views.present}>
        <span className={styles.buildState} data-state={b.state}>{b.state === 'done' ? <Check size={13}/> : b.state === 'failed' ? <X size={13}/> : <LoaderCircle size={13} className={styles.spin}/>}</span>
        <span><b>{b.label}</b><small>{b.state === 'failed' ? b.error : b.state === 'done' ? (b.evidence ? 'Overview and evidence' : 'Overview') : b.state === 'queued' ? 'Waiting to start' : STAGE_LABEL[b.stage ?? ''] ?? 'Building'}</small></span>
      </button></li>)}</ul>}
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
