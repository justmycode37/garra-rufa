'use client';
import { useEffect,useRef,useState } from 'react';
import BodyGraph from './BodyGraph';
import { AboutLink } from './ConnectionExperience';
import { Trash2,ArrowUpRight,ArrowLeft,ArrowRight,BookOpen,Check,ChevronDown,ChevronRight,FileText,Folder,Heart,Home,LogOut,Menu,MessageCircle,Microscope,Network,Plus,Search,ShieldCheck,Sparkles,Stethoscope,Users,X,PanelLeftClose,Bookmark,FlaskConical,Globe } from 'lucide-react';
import { Brand } from './Brand';
import AuthDialog,{roleMeta} from './AuthDialog';
import RoleDialog from './RoleDialog';
import GlobalSearch from './GlobalSearch';
import Dialog from './Dialog';
import { Composer } from './Chat';
import LandingChat from './LandingChat';
import Workspace from './Workspace';
import RecordDialog from './RecordDialog';
import { inferRegion } from '@/lib/knowledge';
import { updateStreamingMessage, type OnAnswerText } from '@/lib/search-stream';
import { readAnimatedSearchResponse } from '@/lib/animated-search';
import { useChatScroll } from './useChatScroll';
import type { User,Role,Region,View,WorkspaceRecord,RecordKind,Message,Disease,Source } from '@/lib/types';
const navItems:{id:View;label:string;icon:typeof Home}[]=[{id:'overview',label:'Home',icon:Home},{id:'atlas',label:'Atlas',icon:Network},{id:'projects',label:'Projects',icon:Folder},{id:'patients',label:'Patients',icon:Stethoscope},{id:'documents',label:'Documents',icon:FileText},{id:'workbench',label:'Research papers',icon:FlaskConical},{id:'community',label:'Community',icon:Users}];
const communityDestinationKey='garra:community-destination';

export default function GarraApp(){
  const [searchOpen,setSearchOpen]=useState(false);
  const [communityId,setCommunityId]=useState('');const pendingCommunity=useRef('');
  const clearCommunityDestination=()=>{pendingCommunity.current='';try{sessionStorage.removeItem(communityDestinationKey);}catch{}};
  const [accountOpen,setAccountOpen]=useState(false);const [authError,setAuthError]=useState('');const [welcomeBusy,setWelcomeBusy]=useState(false);
  const [deleteChat,setDeleteChat]=useState<WorkspaceRecord|'all'|null>(null);const [deletingChat,setDeletingChat]=useState(false);const [deleteChatError,setDeleteChatError]=useState('');
  const [user,setUser]=useState<User|null>(null);const [workspace,setWorkspace]=useState(false);const [view,setView]=useState<View>('overview');const [region,setRegion]=useState<Region>('body');
  const [auth,setAuth]=useState<{mode:'login'|'signup';role?:Role;continueChat?:boolean}|null>(null);const [roleInfo,setRoleInfo]=useState(false);const [records,setRecords]=useState<WorkspaceRecord[]>([]);
  const [messages,setMessages]=useState<Message[]>([]);const [busy,setBusy]=useState(false);const [chatId,setChatId]=useState<string|undefined>();const [attached,setAttached]=useState<WorkspaceRecord[]>([]);const [includeWorkspace,setIncludeWorkspace]=useState(false);
  const [selectedDisease,setSelectedDisease]=useState<Disease|null>(null);const [recordDialog,setRecordDialog]=useState<{record?:Partial<WorkspaceRecord>;kind:RecordKind}|null>(null);const [toast,setToast]=useState('');const [sidebarOpen,setSidebarOpen]=useState(false);const [recordError,setRecordError]=useState('');
  const scrollRef=useRef<HTMLDivElement>(null);const requestGeneration=useRef(0);const activeRequest=useRef<AbortController|null>(null);
  const followAnswer=useChatScroll(scrollRef,workspace&&view==='overview');
  const cancelAnswer=()=>{requestGeneration.current++;activeRequest.current?.abort();activeRequest.current=null;setBusy(false);};
  useEffect(()=>()=>{activeRequest.current?.abort();},[]);
  const loadRecords=async()=>{try{const r=await fetch('/api/records');const d=await r.json();if(!r.ok)throw new Error(d.error);setRecords(d.records);setRecordError('');}catch(e){setRecordError(e instanceof Error?e.message:'Unable to load your workspace.');}};
  useEffect(()=>{
    const params=new URLSearchParams(window.location.search);
    const requestedRole=params.get('role');
    const entryRole=params.get('entry')==='signup'&&(requestedRole==='researcher'||requestedRole==='doctor'||requestedRole==='patient')?requestedRole:null;
    if(entryRole)setAuth({mode:'signup',role:entryRole});
    const connected=params.get('chatgpt')==='connected';const resumeChat=params.get('chat');
    let community=params.get('view')==='community'?params.get('condition')||'':'';
    if(connected)try{community=community||sessionStorage.getItem(communityDestinationKey)||'';}catch{}
    if(!/^[a-z0-9-]{1,80}$/.test(community))community='';
    pendingCommunity.current=community;
    if(community)try{sessionStorage.setItem(communityDestinationKey,community);}catch{}
    if(params.get('chatgpt')==='error'){setAccountOpen(true);setAuthError('ChatGPT sign-in could not be completed or was cancelled. Your previous workspace is unchanged. Please try again.');}
    fetch('/api/auth').then(r=>r.json()).then(async d=>{if(d.user?.chatgpt){setUser(d.user);setWorkspace(!entryRole&&params.get('view')!=='explore');await loadRecords();
      const resume=resumeChat;
      if(resume&&connected){
        const response=await fetch('/api/records');const data=await response.json();
        const chat=response.ok?data.records.find((r:WorkspaceRecord)=>r.id===resume&&r.kind==='chat'):null;
        if(chat){setChatId(chat.id);setMessages(chat.messages||[]);setView('overview');}
      }
      if(community){setCommunityId(community);setWorkspace(true);setView('community');setAuth(null);clearCommunityDestination();}
    }else if(community){setAuth({mode:'signup'});}}).catch(()=>{});
    if(params.has('chatgpt')){params.delete('chatgpt');params.delete('chat');window.history.replaceState(null,'',`${window.location.pathname}${params.size?'?'+params.toString():''}`);}
  },[]);
  useEffect(()=>{if(!toast)return;const id=setTimeout(()=>setToast(''),4500);return()=>clearTimeout(id);},[toast]);
  useEffect(()=>{const key=(e:KeyboardEvent)=>{if((e.metaKey||e.ctrlKey)&&e.key==='k'){e.preventDefault();if(workspace)setSearchOpen(true);else document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus();}};document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key);},[workspace]);
  const navigate=(v:View)=>{if(v==='community')setCommunityId('');setView(v);setSidebarOpen(false);scrollRef.current?.scrollTo({top:0});};
  const openCommunity=(id:string)=>{
    if(user?.chatgpt){setCommunityId(id);setWorkspace(true);setView('community');setSidebarOpen(false);scrollRef.current?.scrollTo({top:0});if(!workspace)void loadRecords();return;}
    pendingCommunity.current=id;try{sessionStorage.setItem(communityDestinationKey,id);}catch{}
    setAuth({mode:'signup'});
  };
  async function confirmChatDeletion(){
    if(!deleteChat||busy||deletingChat)return;
    setDeletingChat(true);setDeleteChatError('');
    try{
      const all=deleteChat==='all';const id=all?undefined:deleteChat.id;
      const response=await fetch('/api/records',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify(all?{scope:'chat-history'}:{id})});
      const result=await response.json();if(!response.ok)throw new Error(result.error||'The chat could not be deleted.');
      setRecords(items=>items.filter(item=>all?item.kind!=='chat':item.id!==id));
      if(all||chatId===id){setMessages([]);setChatId(undefined);setAttached([]);}
      setDeleteChat(null);setToast(all?'Chat history cleared.':'Chat deleted.');
    }catch(error){setDeleteChatError(error instanceof Error?error.message:'Unable to delete this chat.');}
    finally{setDeletingChat(false);}
  }
  const startChat=()=>{cancelAnswer();followAnswer();setMessages([]);setChatId(undefined);setAttached([]);navigate('overview');};
  const closeLandingChat=()=>{cancelAnswer();setMessages([]);setChatId(undefined);setAttached([]);setRegion('body');document.querySelector<HTMLTextAreaElement>('.landing .composer textarea')?.focus();};
  async function enterWorkspace(nextUser:User){
    if(!nextUser.chatgpt){setAccountOpen(true);return;}
    let conversation:WorkspaceRecord|undefined;
    if(auth?.continueChat){
      const response=await fetch('/api/chats',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:messages.slice(-80),chatId:user?.id===nextUser.id?chatId:undefined})});
      const data=await response.json();
      if(!response.ok)throw new Error(data.error||'Unable to carry your conversation into the workspace. Please try again.');
      conversation=data.record;
    }
    cancelAnswer();setUser(nextUser);setAuth(null);setWorkspace(true);setView('overview');setMessages(conversation?.messages||[]);setAttached([]);setIncludeWorkspace(false);setChatId(conversation?.id);setRecords(conversation?[conversation]:[]);loadRecords();
    if(pendingCommunity.current){setCommunityId(pendingCommunity.current);setView('community');clearCommunityDestination();}
  }
  async function openWorkspace(){
    try{
      const response=await fetch('/api/auth');const data=await response.json();
      if(!response.ok||!data.user?.chatgpt){setUser(null);setWorkspace(false);setAuth({mode:'signup',role:'researcher'});return;}
      cancelAnswer();setUser(data.user);setWorkspace(true);setMessages([]);setChatId(undefined);loadRecords();
    }catch{setAccountOpen(true);setAuthError('Please sign in to open your workspace.');}
  }
  async function signOut(){
    const response=await fetch('/api/auth',{method:'DELETE'});const data=await response.json();
    if(!response.ok)throw new Error(data.error||'Unable to sign out.');
    clearCommunityDestination();setCommunityId('');
    cancelAnswer();setUser(null);setWorkspace(false);setAccountOpen(false);setAuth(null);setRecords([]);setMessages([]);setChatId(undefined);setAttached([]);setIncludeWorkspace(false);setSearchOpen(false);setRecordDialog(null);setDeleteChat(null);setAuthError('');
    if(data.warning)setToast(data.warning);
  }
  async function acknowledgePlan(){
    setWelcomeBusy(true);
    try{const response=await fetch('/api/auth/chatgpt',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'acknowledge-plan'})});const data=await response.json();if(!response.ok)throw new Error(data.error);setUser(data.user);}catch{setToast('Could not save your preference. Please try again.');}finally{setWelcomeBusy(false);}
  }
  async function ask(query:string){
    if(busy||activeRequest.current||deletingChat)return;setBusy(true);followAnswer();if(workspace){setView('overview');setSidebarOpen(false);}setRegion(inferRegion(query));
    const generation=++requestGeneration.current;
    const request=new AbortController();activeRequest.current=request;
    const assistantId=crypto.randomUUID();
    const history=messages;const pending:Message[]=[...history,{id:crypto.randomUUID(),role:'user',text:query}];setMessages(pending);
    try{
      const response=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/x-ndjson'},signal:request.signal,body:JSON.stringify({query,surface:workspace?'workspace':'landing',chatId:workspace?chatId:undefined,history:history.slice(-8).map(({id,role,text})=>({id,role,text:text.slice(0,8000)})),fileIds:workspace?attached.map(a=>a.id):[],includeWorkspace:workspace&&includeWorkspace})});
      const data=await readAnimatedSearchResponse(response,text=>{if(generation===requestGeneration.current)setMessages(current=>updateStreamingMessage(current,assistantId,text));},request.signal);
      if(generation!==requestGeneration.current)return;
      const answer=data.messages.at(-1);if(!answer||answer.role!=='assistant')throw new Error('The assistant returned no answer. Please try again.');
      setMessages([...pending,{...answer,id:assistantId}].slice(-80));setRegion(data.region);setChatId(data.chatId);if(workspace)loadRecords();setAttached([]);
    }catch(error){
      if(generation!==requestGeneration.current)return;
      setMessages([...pending,{id:assistantId,role:'assistant',text:'Your question could not be completed. You can still explore the atlas and its original sources.',warning:error instanceof Error?error.message:'Please try again.',mode:'database'}]);
    }finally{if(generation===requestGeneration.current){activeRequest.current=null;setBusy(false);}}
  }
  async function askWithAtlasContext(query:string,history:Message[],onText:OnAnswerText,signal:AbortSignal):Promise<Message>{
    const fileIds=attached.map(file=>file.id);
    const response=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/x-ndjson'},signal,body:JSON.stringify({query,surface:'workspace',history:history.slice(-8).map(({id,role,text})=>({id,role,text:text.slice(0,8000)})),fileIds,includeWorkspace})});
    const data=await readAnimatedSearchResponse(response,onText,signal);
    const answer=(data.messages as Message[]|undefined)?.findLast(message=>message.role==='assistant');
    if(!answer)throw new Error('The assistant returned no answer. Please try again.');
    setAttached(files=>files.filter(file=>!fileIds.includes(file.id)));
    loadRecords();
    return answer;
  }
  async function upload(file:File){
    if(!workspace||!user?.chatgpt){void openWorkspace();throw new Error('Open your workspace to attach a document.');}
    const form=new FormData();form.set('file',file);const r=await fetch('/api/upload',{method:'POST',body:form});const d=await r.json();if(!r.ok)throw new Error(d.error);setRecords(rs=>[d.record,...rs]);setAttached(a=>[...a.filter(x=>x.id!==d.record.id),d.record].slice(-3));setToast('Document saved privately and attached to your next question.');
  }
  const saved=(r:WorkspaceRecord)=>{setRecords(rs=>[r,...rs.filter(x=>x.id!==r.id)]);setToast('Saved to your space.');};
  async function bookmark(s:Source){const r=await fetch('/api/records',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'bookmark',title:s.title.slice(0,160),content:`${s.url}\n\n${s.excerpt}`,status:'Saved'})});const d=await r.json();if(r.ok)saved(d.record);else setToast(d.error||'Unable to save this source.');}
  const saveSuggestion=(m:Message)=>{if(!user){setAuth({mode:'signup',role:'researcher'});return;}if(m.suggestion)setRecordDialog({kind:m.suggestion.kind,record:{title:m.suggestion.title,content:m.suggestion.content}});};
  const viewDisease=(d:Disease)=>{setSelectedDisease(d);setRegion(d.region);};
  const role=user?roleMeta[user.role]:null;
  const chatRecords=records.filter(r=>r.kind==='chat');
  const homeHasConversation=view==='overview'&&(messages.length>0||busy);
  const composerContext={chatGPTPlan:workspace&&view==='overview'?!!user?.chatgpt?.planEnabled:undefined,user:workspace?user:null,onUpload:upload,attached,onDetach:(id:string)=>setAttached(a=>a.filter(f=>f.id!==id)),includeWorkspace,setIncludeWorkspace};
  const composer=<Composer onSubmit={ask} busy={busy} {...composerContext} compact={workspace} placeholder={workspace?user?.role==='researcher'?'Ask about your research…':user?.role==='doctor'?'Ask about a condition or evidence…':'Ask a question…':undefined}/>;
  return <>
    <a className="skip-link" href="#main-content">Skip to content</a>
    {!workspace?<main className="landing" id="main-content">
      
      <header className="landing-nav"><Brand onClick={closeLandingChat}/><div className="nav-right"><AboutLink/><button className="text-button" onClick={()=>setAccountOpen(true)}>{user?.chatgpt?'Account':'Sign in'}</button><button className="primary" onClick={openWorkspace}>My space</button></div></header>
      <div className={`landing-intro ${messages.length||region!=='body'?'searching':''}`}><h1>A way forward.<br/>Together.</h1><p>Rare disease knowledge, connected.</p></div>
      <BodyGraph/>
      <div className="landing-search"><LandingChat onCommunity={openCommunity} messages={messages} busy={busy} composer={composer} onDisease={viewDisease} onClose={closeLandingChat} onContinue={()=>setAuth({mode:'signup',continueChat:true})}/></div>
    </main>:user&&<div className={`workspace-shell ${view==='atlas'?'atlas-shell':''}`}>
      {sidebarOpen&&<button className="sidebar-scrim" aria-label="Close navigation" onClick={()=>setSidebarOpen(false)}/>}
      <aside className={`sidebar ${sidebarOpen?'open':''}`}>
        <div className="sidebar-brand"><Brand small onClick={()=>navigate('overview')}/><button className="mobile-only icon-button" onClick={()=>setSidebarOpen(false)} aria-label="Close navigation"><PanelLeftClose size={18}/></button></div>
        <button className="sidebar-search" onClick={()=>setSearchOpen(true)}><Search size={16}/><span>Search everything</span><kbd>⌘ K</kbd></button>
        <nav className="sidebar-nav" aria-label="Workspace navigation">
          {navItems.filter(n=>['overview','atlas'].includes(n.id)).map(n=><button key={n.id} onClick={()=>navigate(n.id)} className={view===n.id?'active':''}><n.icon size={19}/>{n.label}</button>)}
          <span className="sidebar-group-label">Workspace</span>
          {navItems.filter(n=>!['overview','atlas'].includes(n.id)&&(n.id!=='patients'||user.role==='doctor')).map(n=><button key={n.id} onClick={()=>navigate(n.id)} className={view===n.id?'active':''}><n.icon size={19}/>{n.id==='projects'&&user.role==='patient'?'My journey':n.label}{n.id==='documents'&&records.filter(r=>r.kind==='document').length>0&&<span className="nav-count">{records.filter(r=>r.kind==='document').length}</span>}</button>)}
        </nav>
        <div className="history-heading"><span>Recent chats</span><div className="history-actions">{chatRecords.length>0&&<button aria-label="Clear chat history" title="Clear chat history" disabled={busy||deletingChat} onClick={()=>{setDeleteChatError('');setDeleteChat('all');}}><Trash2 size={14}/></button>}<button aria-label="New conversation" onClick={startChat}><Plus size={15}/></button></div></div>
        <div className="chat-history">{chatRecords.length?chatRecords.map(r=><div key={r.id} className={`chat-history-row ${chatId===r.id&&view==='overview'?'active':''}`}><button className="chat-history-open" onClick={()=>{if(busy||deletingChat)return;setChatId(r.id);setMessages(r.messages||[]);setAttached([]);navigate('overview');}}><MessageCircle size={14}/><span>{r.title}</span></button><button className="chat-history-delete" aria-label={`Delete chat: ${r.title}`} title="Delete chat" disabled={busy||deletingChat} onClick={()=>{setDeleteChatError('');setDeleteChat(r);}}><Trash2 size={13}/></button></div>):<p>Your conversations appear here.</p>}</div>
        <div className="sidebar-bottom"><button className="back-to-atlas" onClick={()=>{cancelAnswer();setWorkspace(false);setMessages([]);setChatId(undefined);setRegion('body');}}><ArrowLeft size={15}/>Back to home</button><button className="workspace-role-switch" onClick={()=>setAuth({mode:'signup',role:user.role})}><span className={`avatar ${role!.color}`}>{role!.label.slice(0,1)}</span><span><b>{role!.label}</b><small>Switch perspective</small></span><ChevronDown size={16}/></button></div>
      </aside>
      <div className="workspace-main">
        <header className="workspace-topbar"><div><button className="mobile-only icon-button" aria-label="Open navigation" onClick={()=>setSidebarOpen(true)}><Menu size={21}/></button><b>{navItems.find(n=>n.id===view)?.label||'Home'}</b></div><button className="text-button chatgpt-account-button" onClick={()=>setAccountOpen(true)}>{user.name}<ChevronDown size={14}/></button></header>
        <div className={`workspace-scroll ${view==='overview'&&!homeHasConversation?'home-scroll':''} ${view==='atlas'?'atlas-scroll':''} ${view==='workbench'?'workbench-scroll':''}`} ref={scrollRef} id="main-content">{recordError&&<div className="service-notice">{recordError}<button onClick={loadRecords}>Retry</button></div>}<Workspace communityId={communityId} onCommunity={openCommunity} user={user} view={view} records={records} region={region} setRegion={setRegion} setView={navigate} onDisease={viewDisease} onNew={kind=>setRecordDialog({kind})} onRecord={r=>setRecordDialog({record:r,kind:r.kind})} onUpload={upload} onAsk={ask} onBookmark={bookmark} messages={messages} busy={busy} onSave={saveSuggestion} composer={composer} composerContext={composerContext} onAskWithContext={askWithAtlasContext}/></div>
        {view!=='atlas'&&view!=='workbench'&&(view!=='overview'||homeHasConversation)&&<div className="workspace-composer">{composer}</div>}
      </div>
    </div>}
    {deleteChat&&<Dialog title={deleteChat==='all'?'Clear chat history?':'Delete this chat?'} onClose={()=>{if(!deletingChat)setDeleteChat(null);}}>
      <p className="dialog-lead">{deleteChat==='all'?'All saved conversations in this workspace will be deleted. Your projects, documents, and patient records will stay.':`“${deleteChat.title}” will be deleted from your history.`}</p>
      <p className="muted">This cannot be undone.</p>
      {deleteChatError&&<p className="form-error" role="alert">{deleteChatError}</p>}
      <div className="button-row"><button className="secondary" disabled={deletingChat} onClick={()=>setDeleteChat(null)}>Cancel</button><button className="primary" disabled={deletingChat||busy} onClick={confirmChatDeletion}>{deletingChat?'Deleting…':deleteChat==='all'?'Clear history':'Delete chat'}</button></div>
    </Dialog>}
    {auth&&!accountOpen&&<RoleDialog initialRole={auth.role} onClose={()=>{clearCommunityDestination();setAuth(null);}} onSuccess={enterWorkspace} user={user} conversation={!busy&&!workspace&&messages.length>=2&&messages.at(-1)?.role==='assistant'?{messages:messages.slice(-80)}:undefined}/>}
    {accountOpen&&<AuthDialog user={user} onClose={()=>{setAccountOpen(false);setAuthError('');}} onSignOut={signOut} conversation={!busy&&!workspace&&messages.length>=2&&messages.at(-1)?.role==='assistant'?{messages:messages.slice(-80)}:undefined} initialError={authError}/>}
    {user?.chatgpt?.needsWelcome&&!accountOpen&&!auth&&<Dialog title="You’re using your ChatGPT plan" onClose={()=>{if(!welcomeBusy)void acknowledgePlan();}}><p className="dialog-lead">Eligible AI questions in Garra Rufa now use your ChatGPT plan and its usage limits. Manage your allowance and access in ChatGPT settings.</p><div className="button-row"><button className="primary" disabled={welcomeBusy} onClick={acknowledgePlan}>{welcomeBusy?'Saving…':'Got it'}</button></div></Dialog>}
    {searchOpen&&<GlobalSearch records={records} onClose={()=>setSearchOpen(false)} onRecord={r=>setRecordDialog({record:r,kind:r.kind})} onDisease={viewDisease} onAsk={ask} onView={navigate}/>}

    {roleInfo&&<Dialog title="Different perspectives. Shared possibilities." onClose={()=>setRoleInfo(false)} wide><p className="muted">One connected platform, with a space that feels right for you.</p><div className="role-info-grid">{(Object.keys(roleMeta) as Role[]).map(r=>{const Icon=roleMeta[r].icon;return <button className={roleMeta[r].color} key={r} onClick={()=>{setRoleInfo(false);setAuth({mode:'signup',role:r});}}><Icon size={31}/><h3>{roleMeta[r].plural}</h3><p>{r==='researcher'?'Explore papers, grow projects, review evidence, and share research.':r==='doctor'?'Centralize patient notes and investigate relevant clinical evidence.':'Keep your documents close, understand your condition, and find your people.'}</p><span>Find your space<ArrowUpRight size={17}/></span></button>;})}</div></Dialog>}
    {selectedDisease&&<DiseaseDialog monochrome={workspace&&view==='atlas'} disease={selectedDisease} onClose={()=>setSelectedDisease(null)} onAsk={q=>{setSelectedDisease(null);ask(q);}} onSave={()=>{if(!user){setSelectedDisease(null);setAuth({mode:'signup',role:'researcher'});return;}setRecordDialog({kind:'project',record:{title:`Exploring ${selectedDisease.shortName}`,diseaseId:selectedDisease.id,content:`Starting reference: ${selectedDisease.source}\n\nResearch question:\n\nEvidence to investigate:\n\nNext step:`}});setSelectedDisease(null);}}/>}
    {recordDialog&&user&&<RecordDialog record={recordDialog.record} kind={recordDialog.kind} user={user} onClose={()=>setRecordDialog(null)} onSaved={saved} onDeleted={id=>{setRecords(rs=>rs.filter(r=>r.id!==id));setAttached(a=>a.filter(r=>r.id!==id));setToast('Item deleted.');}} onReview={r=>{setAttached([r]);navigate('overview');setToast('Document attached. Ask a question to send it to the assistant.');document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus();}}/>}
    {toast&&<div className="toast" role="status"><Check size={17}/>{toast}<button aria-label="Dismiss notification" onClick={()=>setToast('')}><X size={14}/></button></div>}
  </>;
}

function DiseaseDialog({disease:d,onClose,onAsk,onSave,monochrome=false}:{monochrome?:boolean;disease:Disease;onClose:()=>void;onAsk:(q:string)=>void;onSave:()=>void}){
  return <Dialog title={d.name} onClose={onClose} wide className={monochrome?'atlas-disease-dialog':''}><div className="disease-meta"><span className={`category-pill ${d.color}`}>{d.category}</span><span className="source-tag"><BookOpen size={12}/>MedlinePlus Genetics</span></div><p className="dialog-lead">{d.summary}</p><div className="disease-details-grid"><div className="detail-block"><span className="eyebrow">BIOLOGICAL CONNECTION</span><p>{d.mechanism}</p></div><div className={`detail-block ${d.color}`}><span className="eyebrow">ASSOCIATED GENES</span><div className="gene-tags">{d.genes.map(g=><a key={g} href={`https://www.ncbi.nlm.nih.gov/gene/?term=${encodeURIComponent(g+'[sym] AND human[orgn]')}`} target="_blank" rel="noreferrer">{g}<ArrowUpRight size={11}/></a>)}</div><small>Examples; the relevant gene depends on the subtype.</small></div></div><div className="detail-block"><span className="eyebrow">REPORTED FEATURES</span><div className="symptom-tags">{d.symptoms.map(s=><span key={s}>{s}</span>)}</div><p className="fineprint">These features overlap with many other conditions and do not establish a diagnosis.</p></div><a className="source-reference" href={d.source} target="_blank" rel="noreferrer"><BookOpen size={19}/><div><b>Read the original reference</b><small>National Library of Medicine · MedlinePlus Genetics</small></div><ArrowUpRight size={18}/></a><a className="source-reference" href={d.organization.url} target="_blank" rel="noreferrer"><Users size={19}/><div><b>{d.organization.name}</b><small>Find community and patient resources</small></div><ArrowUpRight size={18}/></a><div className="button-row"><button className="primary" onClick={()=>onAsk(`What does the current evidence say about ${d.name}, and what are useful next research questions?`)}>Explore the evidence<ArrowUpRight size={17}/></button><button className="secondary" onClick={onSave}><Plus size={16}/>Start a project</button></div></Dialog>;
}
