'use client';
// Background facts on a selected entry (src/query-test/web_enrich.py): what a symptom means and
// how often it occurs, what a gene / drug / trial is, and for the evidence graph how the entry's
// evidence splits by organism, effect and year, why a paper was read and how well.
import { ArrowUpRight } from 'lucide-react';
import { human, LEVEL_COLOR, NEGATIVE, type DrugInfo, type EvidenceEdge, type EvidenceView, type GeneInfo, type Paper, type PhenotypeInfo, type PresentItem, type TrialInfo } from '@/lib/graph-views';
import graphs from './Graphs.module.css';
import styles from './NodeFacts.module.css';

const firstSentences = (text: string, n = 2) => (text.match(/[^.!?]+[.!?]+(\s|$)/g) ?? [text]).slice(0, n).join('').trim();
const year = (d?: string) => d?.slice(0, 4);

function Facts({ rows }: { rows: [string, React.ReactNode][] }) {
  const shown = rows.filter(([, v]) => v !== undefined && v !== null && v !== '' && v !== false);
  if (!shown.length) return null;
  return <dl className={graphs.facts}>{shown.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;
}

function Meter({ value, label, color = 'var(--ink)' }: { value: number; label: string; color?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return <div className={styles.meter} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={label}>
    <span className={styles.track}><span style={{ width: `${pct}%`, background: color }}/></span><small>{label}</small>
  </div>;
}

/** "about 9 in 10 people" for an HPO frequency */
function inWords(f: number) {
  if (f >= 0.99) return 'almost everyone with the condition';
  if (f >= 0.05) return `about ${Math.max(1, Math.round(f * 10))} in 10 people`;
  return 'fewer than 1 in 20 people';
}

// -- overview (patients) ---------------------------------------------------------------------
export function ItemFacts({ item }: { item: PresentItem }) {
  return <>
    {item.hallmark && <p className={styles.hallmark}><b>★ Key sign</b> Orphanet lists this as a {item.hallmark} of the disease: doctors look for it when they diagnose it.</p>}
    {item.lay && <p className={styles.lay}>In everyday words: <b>{item.lay}</b></p>}
    {item.freq != null && <Meter value={item.freq} label={`Seen in ${inWords(item.freq)}`} color="#b58d80"/>}
    {item.definition && <p className={graphs.body}>{item.definition}</p>}
    {item.similarity != null && <Meter value={item.similarity} label={`${Math.round(item.similarity * 100)}% similar symptoms`} color="#9d93d4"/>}
    {item.gene && <GeneFacts g={item.gene} plain/>}
    {item.drug && <DrugFacts d={item.drug} plain/>}
    {item.trial && <TrialFacts t={item.trial}/>}
  </>;
}

export function GeneFacts({ g, plain }: { g: GeneInfo; plain?: boolean }) {
  return <div className={styles.card}>
    <span className={styles.cardHead}>{plain ? 'About this gene' : 'Gene'}</span>
    {g.function && <p className={graphs.body}>{plain ? firstSentences(g.function) : g.function}</p>}
    <Facts rows={[
      ['Full name', g.name], ['Location', g.locus && `chromosome ${g.locus}`],
      ['Active mostly in', g.tissues?.length ? g.tissues.join(', ') : g.tissue_specificity?.toLowerCase()],
      ['In the cell', g.cell_location?.join(', ')],
      ...(plain ? [] : [
        ['Target class', g.target_class], ['Tractability', g.tractable?.length ? g.tractable.map(t => <span key={t} className={graphs.tag}>{t}</span>) : undefined],
        ['Mouse models', g.mouse ? `${g.mouse} phenotypes (MGI / IMPC)` : undefined],
      ] as [string, React.ReactNode][]),
    ]}/>
    {!plain && g.ot_score != null && <Meter value={g.ot_score} label={`Open Targets association with the disease ${g.ot_score.toFixed(2)}`} color="#7d92b5"/>}
  </div>;
}

export function DrugFacts({ d, plain }: { d: DrugInfo; plain?: boolean }) {
  const approved = d.stage === 'approved';
  return <div className={styles.card}>
    <span className={styles.cardHead}>{plain ? 'About this medicine' : 'Drug'}{d.stage && <span className={approved ? styles.approved : styles.stage}>{approved ? 'approved' : d.stage}</span>}</span>
    <Facts rows={[
      ['Type', d.type?.toLowerCase()], [plain ? 'How it works' : 'Mechanism', d.mechanisms?.join('; ')],
      [plain ? 'Safety notes' : 'Warnings', d.warnings?.length ? d.warnings.join('; ') : undefined],
      ...(plain ? [] : [['ChEMBL', d.chembl && <a href={`https://www.ebi.ac.uk/chembl/explore/compound/${encodeURIComponent(d.chembl)}`} target="_blank" rel="noreferrer">{d.chembl}<ArrowUpRight size={10}/></a>]] as [string, React.ReactNode][]),
    ]}/>
    {plain && approved && <p className="fineprint">Approved for at least one use somewhere; that does not mean it is approved for this disease. Talk to your doctor.</p>}
  </div>;
}

function TrialFacts({ t }: { t: TrialInfo }) {
  const when = t.start || t.end ? `${year(t.start) ?? '?'} → ${year(t.end) ?? '?'}` : undefined;
  return <div className={styles.card}>
    <span className={styles.cardHead}>About this study{t.results && <span className={styles.approved}>results posted</span>}</span>
    {t.official_title && <p className={styles.small}>{t.official_title}</p>}
    <Facts rows={[
      ['Kind', t.type && (t.type === 'interventional' ? 'interventional (tests a treatment)' : t.type === 'observational' ? 'observational (follows patients, no new treatment)' : t.type)],
      ['Phase', t.phases?.join(', ')], ['Runs', when], ['Short name', t.acronym], ['Stopped because', t.why_stopped],
    ]}/>
  </div>;
}

// -- evidence graph (researchers) ------------------------------------------------------------
const ORGANISM_COLOR: Record<string, string> = { human: '#5d7048', mouse: '#a77563', rat: '#b98598', cells: '#a08c4a', pig: '#857ba8' };
const EFFECT_COLOR: Record<string, string> = { positive: LEVEL_COLOR.clinical_trial, mixed: '#bda76a', null: '#b8b1ad', negative: NEGATIVE };

function Split({ title, counts, colors }: { title: string; counts: Record<string, number>; colors: Record<string, string> }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]), total = entries.reduce((s, [, n]) => s + n, 0);
  if (!total) return null;
  return <div className={styles.split}>
    <small>{title}</small>
    <span className={styles.stack}>{entries.map(([k, n]) => <span key={k} title={`${k}: ${n}`} style={{ flexGrow: n, background: colors[k] ?? '#a09488' }}/>)}</span>
    <span className={styles.legend}>{entries.map(([k, n]) => <span key={k}><i style={{ background: colors[k] ?? '#a09488' }}/>{human(k)} {n}</span>)}</span>
  </div>;
}

/** How the quotes behind one entity split by organism and effect, and how recent they are. */
export function EvidenceProfile({ id, edges }: { id: string; edges: EvidenceEdge[] }) {
  const organisms: Record<string, number> = {}, effects: Record<string, number> = {}, years: number[] = [];
  for (const e of edges) if (e.from === id || e.to === id) for (const q of e.evidence) {
    if (q.organism) organisms[q.organism] = (organisms[q.organism] ?? 0) + 1;
    if (q.effect && q.effect !== 'na') effects[q.effect] = (effects[q.effect] ?? 0) + 1;
    if (q.year) years.push(q.year);
  }
  if (!Object.keys(organisms).length && !Object.keys(effects).length) return null;
  years.sort((a, b) => a - b);
  const recent = years.filter(y => y >= new Date().getFullYear() - 5).length;
  return <div className={styles.card}>
    <span className={styles.cardHead}>Evidence profile</span>
    <Split title="Quotes by organism" counts={organisms} colors={ORGANISM_COLOR}/>
    <Split title="Reported effect" counts={effects} colors={EFFECT_COLOR}/>
    {years.length > 0 && <p className="fineprint">Published {years[0]}{years.at(-1) !== years[0] ? `–${years.at(-1)}` : ''} · median {years[Math.floor(years.length / 2)]} · {recent} of {years.length} quotes from the last 5 years</p>}
  </div>;
}

export function EvidenceNodeFacts({ node, edges }: { node: { id: string; gene?: GeneInfo; drug?: DrugInfo; phenotype?: PhenotypeInfo }; edges: EvidenceEdge[] }) {
  return <>
    {node.phenotype?.definition && <p className={graphs.body}>{node.phenotype.definition}{node.phenotype.lay && <small className={styles.small}> Lay term: {node.phenotype.lay}</small>}</p>}
    {node.gene && <GeneFacts g={node.gene}/>}
    {node.drug && <DrugFacts d={node.drug}/>}
    <EvidenceProfile id={node.id} edges={edges}/>
  </>;
}

const CONNECTION: Record<string, string> = {
  same_disease: 'about the disease itself', shared_phenotype: 'a disease with shared symptoms', shared_mechanism: 'a shared mechanism',
  related_disease: 'a related disease', method_transfer: 'a method that could transfer', none: 'no clear connection',
};

export function PaperFacts({ p }: { p: Paper }) {
  const read = p.text_source === 'abstract' ? 'abstract only' : `full text (${p.text_source})${p.truncated ? ', truncated' : ''}`;
  return <div className={styles.card}>
    <span className={styles.cardHead}>Why it was read</span>
    {p.why && <p className={graphs.body}>{p.why}</p>}
    <Facts rows={[
      ['Relevance', p.relevance != null ? `${p.relevance}/3` : undefined], ['Connection', p.connection && (CONNECTION[p.connection] ?? human(p.connection))],
      ['Offers', p.solution_types?.length ? p.solution_types.map(s => <span key={s} className={graphs.tag}>{human(s)}</span>) : undefined],
      ['Read', `${read}${p.chars ? ` · ${Math.round(p.chars / 1000)}k characters` : ''}`],
      ['Quotes', p.quotes != null ? <>{p.quotes} extracted{p.unverified ? <span className={graphs.neg}> · {p.unverified} not found verbatim</span> : ' · all verified verbatim'}</> : undefined],
    ]}/>
  </div>;
}

/** The literature funnel: screened → judged relevant → read, and the best papers left unread. */
export function ScreeningFunnel({ data }: { data: EvidenceView }) {
  const s = data.screening, x = data.pipeline?.extraction;
  if (!s) return null;
  const max = Math.max(1, s.screened);
  return <details className={styles.funnel}>
    <summary>Literature funnel <small>{s.screened} screened · {s.included} relevant · {s.read} read</small></summary>
    {([['screened', s.screened], ['judged relevant', s.included], ['read in full', s.read]] as const).map(([k, n]) =>
      <Meter key={k} value={n / max} label={`${n} ${k}`} color="#7d92b5"/>)}
    <Split title="Relevant papers by connection" counts={s.by_connection} colors={{ same_disease: '#5d7048', shared_phenotype: '#b58d80', shared_mechanism: '#7d92b5', related_disease: '#9d93d4', method_transfer: '#789e95' }}/>
    {x && <p className="fineprint">{x.quotes} quotes extracted, {x.unverified_quotes ?? 0} not found verbatim{x.edges_dropped_unverified ? `, ${x.edges_dropped_unverified} edges dropped for it` : ''}.</p>}
    {s.unread.length > 0 && <>
      <small className={styles.small}>Relevant but not read ({s.included - s.read}), best first: a reading list beyond this graph</small>
      <div className={styles.unread}>{s.unread.slice(0, 25).map(u => {
        const pmid = u.key.startsWith('PMID:') ? u.key.slice(5) : null;
        return <p key={u.key} className={graphs.paperLine} title={u.why}>
          {pmid ? <a href={`https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(pmid)}/`} target="_blank" rel="noreferrer">{u.key}<ArrowUpRight size={10}/></a> : u.key}{' '}
          <small>{u.relevance != null && `${u.relevance}/3 · `}{u.connection && `${CONNECTION[u.connection] ?? human(u.connection)} · `}{u.title}</small>
        </p>;
      })}</div>
    </>}
  </details>;
}
