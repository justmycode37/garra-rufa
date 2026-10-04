'use client';

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { LoaderCircle, Plus, Sparkles, X } from 'lucide-react';
import { atlasRegions } from '@/lib/atlas-graph';
import { MAX_QUERY, organFindingsSchema, organQuery, readTerm, type OrganFindings, type OrganTerm } from '@/lib/organ-query';
import styles from './OrganGraphBuilder.module.css';

let findings: Promise<OrganFindings> | undefined;
function loadFindings() {
  findings ??= fetch('/organ-findings.json').then(async response => {
    if (!response.ok) throw new Error();
    return organFindingsSchema.parse(await response.json());
  });
  findings.catch(() => { findings = undefined; });
  return findings;
}

type Access = { signedIn: boolean; enabled: boolean; notice?: string };

/** An organ picked in the atlas plus the terms the user adds become one graph build. */
export default function OrganGraphBuilder({ regionId }: { regionId: string }) {
  const router = useRouter();
  const region = atlasRegions[regionId];
  const organ = { label: region.label, hpo: region.hpo };
  const [data, setData] = useState<OrganFindings>();
  const [access, setAccess] = useState<Access>();
  const [terms, setTerms] = useState<OrganTerm[]>([]);
  const [draft, setDraft] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const info = data?.regions[regionId];

  useEffect(() => { setTerms([]); setDraft(''); setMessage(''); }, [regionId]);
  useEffect(() => {
    let live = true;
    loadFindings().then(next => { if (live) setData(next); }).catch(() => {});
    Promise.all([
      fetch('/api/auth').then(r => r.json()).then(d => !!d.user && !d.user.guest).catch(() => false),
      fetch('/api/graphs').then(r => r.json()).catch(() => ({ enabled: false })),
    ]).then(([signedIn, builds]) => { if (live) setAccess({ signedIn, enabled: !!builds.enabled, notice: builds.notice }); });
    return () => { live = false; };
  }, []);

  function add(term: OrganTerm) {
    // a picked finding goes into the query by its HP id, so its label may hold any punctuation
    const { term: text, error } = term.id ? { term: term.text, error: undefined } : readTerm(term.text);
    if (error) { setMessage(error); return false; }
    if (!text) return false;
    if (terms.some(other => other.text.toLowerCase() === text.toLowerCase())) { setDraft(''); return false; }
    const next = [...terms, { ...term, text }];
    const check = organQuery(organ, next);
    if (check.error) { setMessage(check.error); return false; }
    setTerms(next); setDraft(''); setMessage('');
    return true;
  }

  function keyDown(event: KeyboardEvent<HTMLInputElement>) {
    if ((event.key === 'Enter' || event.key === ',' || event.key === ';') && draft.trim()) { event.preventDefault(); add({ text: draft }); }
    else if (event.key === 'Backspace' && !draft && terms.length) setTerms(terms.slice(0, -1));
  }

  async function build(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    // a term still in the field counts too
    let all = terms;
    if (draft.trim()) {
      const { term, error } = readTerm(draft);
      if (error) { setMessage(error); return; }
      if (term && !terms.some(other => other.text.toLowerCase() === term.toLowerCase())) all = [...terms, { text: term }];
    }
    const request = organQuery(organ, all);
    if (request.error) { setMessage(request.error); input.current?.focus(); return; }
    setTerms(all); setDraft('');
    setBusy(true); setMessage('');
    try {
      const response = await fetch('/api/graphs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: request.query, label: request.label }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'The graph could not be started.');
      router.push(`/graphs/overview?run=${encodeURIComponent(body.build.id)}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'The graph could not be started.');
      setBusy(false);
    }
  }

  const ready = terms.length > 0 || !!readTerm(draft).term;
  const blocked = access && (!access.enabled || !access.signedIn);
  const used = new Set(terms.map(term => term.text.toLowerCase()));
  const suggestions = (info?.findings ?? []).filter(finding => !used.has(finding.label.toLowerCase())).slice(0, 14);

  return <aside className={styles.panel} aria-label={`Build a graph for ${region.label}`}>
    <header className={styles.header}>
      <span className={styles.eyebrow}>Build an overview graph</span>
      <h2>{region.label}</h2>
      <p>{info ? `${info.diseases.toLocaleString()} diseases have a recorded finding here. ` : ''}Add symptoms, genes or findings to narrow them down. The graph is built around the diseases that best match the organ and your terms.</p>
    </header>
    <form className={styles.form} onSubmit={build}>
      <div className={styles.field} onClick={() => input.current?.focus()}>
        <span className={styles.organ} title={`${info?.hpo_label ?? region.label} (${region.hpo}) is always included`}>{region.label}</span>
        {terms.map((term, index) => <span key={term.text} className={styles.term} title={term.id}>
          {term.text}<button type="button" onClick={() => setTerms(terms.filter((_, i) => i !== index))} aria-label={`Remove ${term.text}`}><X size={11}/></button>
        </span>)}
        <input ref={input} value={draft} onChange={e => { setDraft(e.target.value); setMessage(''); }} onKeyDown={keyDown} disabled={busy}
          placeholder={terms.length ? 'Add another term' : 'e.g. chest pain, MYH7, fainting'} aria-label={`Symptoms, genes or findings for ${region.label}`} maxLength={60}/>
      </div>
      <p className={styles.hint}>Press Enter or comma after each term. At least one term besides the organ is needed.</p>
      {suggestions.length > 0 && <div className={styles.suggestions} aria-label={`Common ${region.label.toLowerCase()} findings`}>
        <span>Common findings</span>
        {suggestions.map(finding => <button type="button" key={finding.id} onClick={() => add({ text: finding.label, id: finding.id })} disabled={busy} title={`${finding.id} · ${finding.diseases.toLocaleString()} diseases`}>
          <Plus size={11} aria-hidden="true"/>{finding.label}
        </button>)}
      </div>}
      {message && <p className={styles.message} role="alert">{message}</p>}
      {blocked ? <p className={styles.message} role="status">
        {!access.enabled ? `Building graphs is unavailable right now. ${access.notice ?? ''}` : <><Link href="/?entry=signup&role=researcher">Sign in</Link> to build a graph.</>}
      </p> : <button className={styles.build} disabled={!ready || busy || !access}>
        {busy ? <LoaderCircle size={15} className={styles.spin}/> : <Sparkles size={15}/>}{busy ? 'Starting…' : 'Build overview graph'}
      </button>}
      <p className={styles.provenance}>Built from public rare-disease sources; takes a few minutes, while a query built before opens from the cache at once. Do not enter personal or patient details. Query limit {MAX_QUERY} characters.</p>
    </form>
  </aside>;
}
