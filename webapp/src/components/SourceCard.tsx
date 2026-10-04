'use client';

import type { Source } from '@/lib/types';
import { safeSourceUrl } from '@/lib/source-schema';

const labels: Record<Source['kind'], string> = { paper: 'Research paper', reference: 'Research record', contact: 'Expert contact', organization: 'Organisation', trial: 'Clinical trial', workspace: 'Your workspace' };
const providerNames: Record<string, string> = { pubmed: 'PubMed', europepmc: 'Europe PMC', pubtator: 'PubTator', litvar: 'LitVar', mondo: 'MONDO', monarch: 'Monarch', clinicaltrials: 'ClinicalTrials.gov', orphanet_groups: 'Orphanet', ern: 'European Reference Networks' };

export default function SourceCard({ source, number, children }: { source: Source; number?: number; children?: React.ReactNode }) {
  const url = safeSourceUrl(source.url);
  const provider = source.providers?.map(name => providerNames[name] || name).join(' · ');
  const subtitle = source.kind === 'paper'
    ? [source.preprint ? 'Preprint' : undefined, source.journal || provider || 'Research paper', source.year].filter(Boolean).join(' · ')
    : [source.recordType || labels[source.kind], source.location || provider].filter(Boolean).join(' · ');
  const content = <><h3 title={source.title}>{source.title}</h3><p className="evidence-card-subtitle">{subtitle}</p></>;
  return <article className="evidence-card" aria-label={`${labels[source.kind]}${number === undefined ? '' : ` [${number}]`}: ${source.title}`}>
    {url ? <a className="evidence-card-link" href={url} target={url.startsWith('/') ? undefined : '_blank'} rel="noreferrer">{content}</a> : <div className="evidence-card-link">{content}</div>}
    {children}
  </article>;
}
