'use client';
import { useRef,useState } from 'react';
import { ArrowUpRight,Plus,Search,Folder,FileText,Users,BookOpen,Upload,ArrowRight,Bookmark,Stethoscope,LoaderCircle,Check } from 'lucide-react';
import dynamic from 'next/dynamic';
import { ChatMessages, type ComposerContext } from './Chat';
import ThinkingIndicator from './ThinkingIndicator';
import Community from './Community';
import type { User,View,WorkspaceRecord,Disease,Region,RecordKind,Message,Source } from '@/lib/types';
const DiseaseAtlas=dynamic(()=>import('./DiseaseAtlas'),{ssr:false});
type Props={user:User;view:View;records:WorkspaceRecord[];region:Region;setRegion:(r:Region)=>void;setView:(v:View)=>void;onDisease:(d:Disease)=>void;onNew:(k:RecordKind)=>void;onRecord:(r:WorkspaceRecord)=>void;onUpload:(f:File)=>Promise<void>;onAsk:(q:string)=>void;onBookmark:(s:Source)=>void;messages:Message[];busy:boolean;onSave:(m:Message)=>void;composer:React.ReactNode;composerContext:ComposerContext;onAskWithContext:(query:string,history:Message[])=>Promise<Message>};
export default function Workspace(p:Props){
  const {user,view,records,onDisease,onNew,onRecord,setView,onAsk}=p;const fileRef=useRef<HTMLInputElement>(null);const [filter,setFilter]=useState('');const [uploading,setUploading]=useState(false);const [uploadError,setUploadError]=useState('');
  const openNew=(kind:RecordKind)=>kind==='document'?fileRef.current?.click():onNew(kind);
  const fileInput=<><input hidden ref={fileRef} type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.txt,.csv" onChange={async e=>{const f=e.target.files?.[0];if(!f)return;setUploading(true);setUploadError('');try{await p.onUpload(f);}catch(e){setUploadError(e instanceof Error?e.message:'Upload failed.');}finally{setUploading(false);e.target.value='';}}}/>{uploadError&&<p role="alert" className="form-error">{uploadError}</p>}</>;
  if(view==='overview'&&(p.messages.length>0||p.busy))return <div className="workspace-chat"><ChatMessages messages={p.messages} busy={p.busy} onDisease={onDisease} onSave={p.onSave}/></div>;
  if(view==='atlas')return <DiseaseAtlas onDisease={onDisease} composerContext={p.composerContext} onAskWithContext={p.onAskWithContext}/>;
  if(view==='workbench')return <Workbench onBookmark={p.onBookmark} onAsk={onAsk} records={records} onRecord={onRecord}/>;
  if(view==='community')return <Community user={user} onAsk={onAsk}/>;
  if(['projects','patients','documents'].includes(view)){
    const kind=view==='projects'?'project':view==='patients'?'patient':'document';
    const title=kind==='document'?'Documents':kind==='patient'?'Patients':user.role==='patient'?'My journey':'Projects';
    const items=records.filter(r=>(r.kind===kind||(kind==='document'&&r.kind==='note'))&&(r.title+' '+r.content).toLowerCase().includes(filter.toLowerCase()));
    return <div className="collection-page">{fileInput}
      <div className="section-heading"><div><h1>{title}</h1></div><span className="collection-count">{items.length} {items.length===1?'item':'items'}</span></div>
      <div className="collection-actions">
        <button onClick={()=>openNew(kind)} disabled={uploading}>{uploading?<LoaderCircle size={23} className="spin"/>:kind==='document'?<Upload size={23}/>:<Plus size={23}/>}<span>{kind==='document'?'Upload files':`New ${kind}`}</span></button>
        <button onClick={()=>onNew('note')}><FileText size={23}/><span>Create a note</span></button>
        <button onClick={()=>setView('workbench')}><BookOpen size={23}/><span>Find evidence</span></button>
      </div>
      <div className="collection-toolbar"><div className="search-box"><Search size={18}/><input aria-label={`Search ${view}`} placeholder={`Search ${title.toLowerCase()}…`} value={filter} onChange={e=>setFilter(e.target.value)}/></div></div>
      {items.length?<div className="collection-list"><div className="collection-list-head"><span>Name</span><span>Details</span><span>Updated</span><span/></div>{items.map(r=><button className="collection-list-row" key={r.id} onClick={()=>onRecord(r)}><span className="record-name"><span className="record-row-icon">{r.kind==='patient'?<Users size={20}/>:r.kind==='project'?<Folder size={20}/>:<FileText size={20}/>}</span><span><b>{r.title}</b><small>{r.kind==='document'?r.fileName:r.kind}</small></span></span><span className="record-row-status">{r.readOnly?'Shared with you':r.visibility==='public'?'Published':r.status}</span><span className="record-row-date">{new Date(r.updatedAt).toLocaleDateString('en-GB',{day:'numeric',month:'short'})}</span><ArrowUpRight size={16}/></button>)}</div>:<div className="collection-empty"><span className="empty-tile">{kind==='document'?<FileText size={25}/>:kind==='patient'?<Users size={25}/>:<Folder size={25}/>}</span><h2>{filter?'No matching items':`No ${title.toLowerCase()} yet`}</h2><p>{filter?'Try another name or keyword.':kind==='document'?'Upload a file or create a note to get started.':kind==='patient'?'Add a patient to bring their information together.':'Create your first project when an idea takes shape.'}</p></div>}
    </div>;
  }
  const quickActions=[
    {label:user.role==='doctor'?'Patients':user.role==='patient'?'My journey':'Projects',icon:user.role==='doctor'?Stethoscope:Folder,action:()=>setView(user.role==='doctor'?'patients':'projects')},
    {label:'Upload a file',icon:Upload,action:()=>openNew('document')},
    {label:user.role==='patient'?'Community':'Research papers',icon:user.role==='patient'?Users:BookOpen,action:()=>setView(user.role==='patient'?'community':'workbench')},
  ];
  const recent=records.filter(r=>r.kind!=='chat').slice(0,5);
  return <div className="workspace-home">{fileInput}
    <section className="home-prompt"><h1>{user.role==='researcher'?'What would you like to discover?':user.role==='doctor'?'What would you like to explore?':'What would you like to understand?'}</h1>{p.composer}</section>
    <div className="home-actions">{quickActions.map(({label,icon:Icon,action})=><button key={label} onClick={action}><span><Icon size={17} strokeWidth={1.5}/></span>{label}</button>)}</div>
    {recent.length>0&&<section className="home-recents" aria-labelledby="recent-work-title"><h2 id="recent-work-title">Recent work</h2><div className="recent-list">{recent.map(r=><button key={r.id} onClick={()=>onRecord(r)}><FileText size={17}/><span><b>{r.title}</b><small>{r.kind} · {new Date(r.updatedAt).toLocaleDateString('en-GB',{day:'numeric',month:'short'})}</small></span><ArrowUpRight size={15}/></button>)}</div></section>}
  </div>;
}

export function MiniNetwork({variant=0}:{variant?:number}){const coords=[[34,80],[86,35],[95,110],[160,62],[172,130],[225,36],[258,98],[220,160],[130,165]];return <svg viewBox="0 0 300 200" fill="none" aria-hidden="true">{coords.map(([x,y],i)=><g key={i}>{coords.slice(i+1).map(([x2,y2],j)=>(i+j+variant)%3!==0&&Math.hypot(x-x2,y-y2)<135?<path key={j} d={`M${x} ${y}L${x2} ${y2}`} stroke="currentColor" opacity=".2"/>:null)}<circle cx={x} cy={y} r={i%3===0?5:3} fill="currentColor"/><circle cx={x} cy={y} r={i===variant+2?18:0} stroke="currentColor" opacity=".25"/></g>)}</svg>;}
export function DiseaseCard({disease:d,onClick}:{disease:Disease;onClick:()=>void}){return <button className="disease-list-card" onClick={onClick}><span className={`disease-symbol ${d.color}`}><MiniNetwork/></span><div><small>{d.category}</small><h3>{d.shortName}</h3><p>{d.genes.slice(0,3).join(' · ')}</p></div><ArrowUpRight size={17}/></button>;}
function EmptyState({icon:Icon,title,description,action,actionLabel}:{icon:typeof Folder;title:string;description:string;action?:()=>void;actionLabel?:string}){return <div className="empty-state"><span className="empty-art"><Icon size={42}/></span><h2>{title}</h2><p>{description}</p>{action&&<button className="primary" onClick={action}>{actionLabel}<Plus size={16}/></button>}</div>;}

function Workbench({onBookmark,onAsk,records,onRecord}:{onBookmark:(s:Source)=>void;onAsk:(q:string)=>void;records:WorkspaceRecord[];onRecord:(r:WorkspaceRecord)=>void}){
  const [query,setQuery]=useState('');const [sources,setSources]=useState<Source[]>([]);const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [searched,setSearched]=useState(false);
  async function search(e:React.FormEvent){e.preventDefault();setBusy(true);setError('');setSearched(true);try{const r=await fetch(`/api/knowledge?q=${encodeURIComponent(query)}&papers=1`);const d=await r.json();if(!r.ok)throw new Error(d.error);setSources(d.sources);if(!d.literatureAvailable)setError('Live literature is temporarily unavailable. Reference records are shown below.');}catch{setError('Search could not finish. Please try again.');}finally{setBusy(false);}}
  return <div><div className="section-heading"><div><h1>Research papers</h1></div></div><form className="workbench-search glass" onSubmit={search}><Search size={20}/><input aria-label="Search literature" value={query} onChange={e=>setQuery(e.target.value)} placeholder="A condition or gene, like Fabry or MECP2…" required minLength={2}/><button className="primary" disabled={busy}>{busy?'Researching…':'Find evidence'}<ArrowRight size={17}/></button></form>{error&&<p className="service-notice">{error}</p>}
    {busy&&<ThinkingIndicator label="Researching…"/>}
    {sources.length>0?<div className="paper-list">{sources.map(s=><article className="paper-card" key={s.id}><span className="eyebrow">{s.kind==='paper'?'EUROPE PMC · RESEARCH PAPER':'MEDLINEPLUS GENETICS'}{s.year?' · '+s.year:''}</span><h3><a href={s.url} target="_blank" rel="noreferrer">{s.title}<ArrowUpRight size={16}/></a></h3><p>{s.excerpt.slice(0,310)}{s.excerpt.length>310?'…':''}</p><div className="button-row"><button className="text-button" onClick={()=>onBookmark(s)}><Bookmark size={15}/>Save to workbench</button><button className="text-button" onClick={()=>onAsk(`Explain the evidence about ${query}, including this source: ${s.title}.`)}>Explore with assistant<ArrowUpRight size={15}/></button></div></article>)}</div>:searched&&!busy?<EmptyState icon={Search} title="No matching evidence in this collection." description="Try a condition from the atlas. The current collection contains eight conditions; an empty result is a coverage gap, not proof that research does not exist."/>:<div className="workbench-sources"><span className="source-tag"><Check size={13}/>MedlinePlus Genetics</span><span className="source-tag"><Check size={13}/>Europe PMC</span></div>}
    <div className="section-row"><h2>Saved evidence</h2></div>{records.filter(r=>r.kind==='bookmark'||r.kind==='note').length?<div className="recent-list">{records.filter(r=>r.kind==='bookmark'||r.kind==='note').map(r=><button key={r.id} onClick={()=>onRecord(r)}><Bookmark size={19}/><span><b>{r.title}</b><small>{r.kind==='bookmark'?'Saved source':'Research note'}</small></span><ArrowUpRight size={17}/></button>)}</div>:<p className="muted">Save a source or a research note to begin your collection.</p>}</div>;
}

