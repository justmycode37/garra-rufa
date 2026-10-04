'use client';
import { useState } from 'react';
import { ArrowRight, FileText, Search, Sparkles } from 'lucide-react';
import Dialog from './Dialog';
import { diseases } from '@/lib/knowledge';
import type { Disease, WorkspaceRecord, View } from '@/lib/types';

export default function GlobalSearch({records,onClose,onRecord,onDisease,onAsk,onView}:{records:WorkspaceRecord[];onClose:()=>void;onRecord:(r:WorkspaceRecord)=>void;onDisease:(d:Disease)=>void;onAsk:(q:string)=>void;onView:(v:View)=>void}) {
  const [query,setQuery]=useState('');
  const term=query.trim().toLowerCase();
  const files=records.filter(r=>r.kind!=='chat'&&`${r.title} ${r.content}`.toLowerCase().includes(term)).slice(0,5);
  const conditions=term?diseases.filter(d=>`${d.name} ${d.genes.join(' ')} ${d.symptoms.join(' ')}`.toLowerCase().includes(term)).slice(0,5):[];
  const open=(action:()=>void)=>{onClose();action();};
  return <Dialog title="Search your space" onClose={onClose}>
    <form className="global-search-form" onSubmit={e=>{e.preventDefault();if(term.length>1)open(()=>onAsk(query));}}><Search size={19}/><input autoFocus aria-label="Search everything" placeholder="Projects, diseases, genes, documents…" value={query} onChange={e=>setQuery(e.target.value)}/></form>
    <div className="global-search-results">
      {!!files.length&&<><span className="search-group-label">Your work</span>{files.map(r=><button key={r.id} onClick={()=>open(()=>onRecord(r))}><FileText size={17}/><span>{r.title}<small>{r.kind}</small></span><ArrowRight size={15}/></button>)}</>}
      {!!conditions.length&&<><span className="search-group-label">Atlas</span>{conditions.map(d=><button key={d.id} onClick={()=>open(()=>onDisease(d))}><span className={`search-condition-dot ${d.color}`}/><span>{d.shortName}<small>{d.genes.join(' · ')}</small></span><ArrowRight size={15}/></button>)}</>}
      {!term&&!files.length&&<div className="search-shortcuts">{([{view:'projects',label:'Projects'},{view:'documents',label:'Documents'},{view:'atlas',label:'Atlas'},{view:'workbench',label:'Research papers'}] as {view:View;label:string}[]).map(item=><button key={item.view} onClick={()=>open(()=>onView(item.view))}>{item.label}<ArrowRight size={15}/></button>)}</div>}
      {term&&!files.length&&!conditions.length&&<p className="search-empty">No matching items in your space.</p>}
      {term.length>1&&<button className="search-ask" onClick={()=>open(()=>onAsk(query))}><Sparkles size={18}/><span>Ask the assistant about “{query}”</span><ArrowRight size={15}/></button>}
    </div>
  </Dialog>;
}
