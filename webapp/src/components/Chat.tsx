'use client';
import { useRef,useState } from 'react';
import { ArrowUp,Plus,X,Paperclip,LoaderCircle,ArrowUpRight,BookOpen,ChevronDown,Check,FolderPlus } from 'lucide-react';
import { GarraMark } from './Brand';
import ThinkingIndicator, { FishAnimation } from './ThinkingIndicator';
import VoiceInput from './VoiceInput';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Message,WorkspaceRecord,User,Disease } from '@/lib/types';

type ComposerProps = {
  onSubmit: (query: string) => void;
  busy: boolean;
  user?: User | null;
  onUpload?: (file: File) => Promise<void>;
  attached?: WorkspaceRecord[];
  onDetach?: (id: string) => void;
  includeWorkspace?: boolean;
  setIncludeWorkspace?: (value: boolean) => void;
  placeholder?: string;
  compact?: boolean;
  voiceEnabled?: boolean;
  contextLabel?: string;
  chatGPTPlan?: boolean;
};

export type ComposerContext = Pick<ComposerProps, 'user' | 'onUpload' | 'attached' | 'onDetach' | 'includeWorkspace' | 'setIncludeWorkspace'>;

export function Composer({onSubmit,busy,user,onUpload,attached=[],onDetach,includeWorkspace=false,setIncludeWorkspace,placeholder,compact=false,voiceEnabled=true,contextLabel,chatGPTPlan}:ComposerProps){
  const [text,setText]=useState('');const [voiceBusy,setVoiceBusy]=useState(false);const [voiceNotice,setVoiceNotice]=useState('');const [uploading,setUploading]=useState(false);const [error,setError]=useState('');const ref=useRef<HTMLTextAreaElement>(null);const fileRef=useRef<HTMLInputElement>(null);
  const submit=()=>{if(text.trim().length<2||busy||voiceBusy||uploading)return;setVoiceNotice('');onSubmit(text.trim());setText('');if(ref.current)ref.current.style.height='auto';};
  return <div className={`composer-wrap ${compact?'in-workspace':''}`}>
    <form className="composer glass" onSubmit={e=>{e.preventDefault();submit();}}>
      {attached.length>0&&<div className="attachments">{attached.map(f=><span key={f.id}><Paperclip size={13}/>{f.title}<button type="button" aria-label={`Remove ${f.title}`} onClick={()=>onDetach?.(f.id)}><X size={12}/></button></span>)}</div>}
      <textarea aria-label="Ask Garra Rufa" ref={ref} value={text} maxLength={16000} rows={1} placeholder={placeholder||'Ask about a rare disease…'} onChange={e=>{setText(e.target.value);e.target.style.height='auto';e.target.style.height=Math.min(e.target.scrollHeight,130)+'px';}} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();submit();}}}/>
      <div className="composer-bottom"><div className="composer-tools">{onUpload&&<><input hidden ref={fileRef} type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.txt,.csv" onChange={async e=>{const f=e.target.files?.[0];if(!f)return;setUploading(true);setError('');try{await onUpload(f);}catch(e){setError(e instanceof Error?e.message:'Upload failed.');}finally{setUploading(false);e.target.value='';}}}/><button type="button" className="attach-button" aria-label="Attach a document" onClick={()=>fileRef.current?.click()} disabled={uploading||attached.length>=3}>{uploading?<LoaderCircle className="spin" size={19}/>:<Plus size={21}/>}</button></>}{(!compact||contextLabel)&&<><span className="composer-divider"/><span className="assistant-label"><GarraMark size={16}/> {busy?'Thinking…':contextLabel||'Ask anything'}</span></>}{user&&setIncludeWorkspace&&<button type="button" className={`workspace-toggle ${includeWorkspace?'selected':''}`} onClick={()=>setIncludeWorkspace(!includeWorkspace)} title="Allow this question to search your projects, patient notes, and shared records" aria-pressed={includeWorkspace}>{includeWorkspace?<Check size={12}/>:<Plus size={12}/>} My workspace</button>}</div><div className="composer-actions">{voiceEnabled&&<VoiceInput disabled={busy||uploading} onBusyChange={setVoiceBusy} onTranscript={transcript=>{const combined=[ref.current?.value.trim(),transcript].filter(Boolean).join(" ");if(combined.length>16000)throw new Error("The combined question is too long. Shorten the text before transcribing again.");setText(combined);setError('');setVoiceNotice("Transcript added. Review it, then send your question.");requestAnimationFrame(()=>{if(ref.current){ref.current.focus();ref.current.style.height="auto";ref.current.style.height=Math.min(ref.current.scrollHeight,130)+"px";}});}}/>}<button className={`send-button${busy ? ' is-thinking' : ''}`} type="submit" disabled={text.trim().length<2||busy||voiceBusy||uploading} aria-label={busy ? 'Answer in progress' : 'Send question'} aria-busy={busy}>{busy?<FishAnimation compact/>:<ArrowUp size={21}/>}</button></div></div>
    </form>
    {voiceNotice&&<p className="voice-transcript-note" role="status">{voiceNotice}</p>}
    {error&&<p className="form-error" role="alert">{error}</p>}
    {attached.length>0&&<p className="composer-note">Attached files are sent to OpenAI only when you send your question.</p>}
    {chatGPTPlan!==undefined&&<div className="chatgpt-plan-status"><span>{chatGPTPlan?'Using ChatGPT plan':'ChatGPT plan access is not enabled'}</span><a href="https://chatgpt.com/settings/usage" target="_blank" rel="noreferrer">Manage usage ↗</a></div>}
  </div>;
}

export function Text({text}:{text:string}){return <div className="answer-text"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{a:({node,...props})=><a {...props} target={props.href?.startsWith('/')?undefined:'_blank'} rel="noreferrer"/>,table:({node,...props})=><div className="answer-table"><table {...props}/></div>,img:({alt})=><span>{alt}</span>}}>{text}</Markdown></div>;}

export function Answer({message,onDisease,onSave,plain=false}:{message:Message;onDisease:(d:Disease)=>void;onSave?:(m:Message)=>void;plain?:boolean}){
  const [expanded,setExpanded]=useState(false);
  return <div className="answer">
    <Text text={message.text}/>
    {message.warning&&<div className="service-notice" role="status">{message.warning}</div>}
    {!plain&&!!message.diseases?.length&&<div className="related-diseases">{message.diseases.slice(0,4).map(d=><button key={d.id} onClick={()=>onDisease(d)}><i className={d.color}/>{d.shortName}<ArrowUpRight size={13}/></button>)}</div>}
    {plain&&!!message.steps?.length&&<div className="answer-text answer-next-steps"><h3>Next steps</h3><ul>{message.steps.map((step,index)=><li key={index}><Text text={step}/></li>)}</ul></div>}
    {!!message.sources?.length&&<div className="sources-block"><button className="sources-toggle" onClick={()=>setExpanded(!expanded)} aria-expanded={expanded}><BookOpen size={15}/>{message.sources.length} sources<ChevronDown size={14} className={expanded?'rotate':''}/></button>{expanded&&<ol className="source-list">{message.sources.map(s=><li key={s.id}><a href={s.url} target={s.kind==='workspace'?undefined:'_blank'} rel="noreferrer">{s.title}<ArrowUpRight size={13}/></a><small>{s.kind==='workspace'?'Your workspace':s.kind==='paper'?`Research paper${s.year?' · '+s.year:''}`:'MedlinePlus Genetics'}</small></li>)}</ol>}</div>}
    {!plain&&!!message.steps?.length&&<div className="next-steps"><span className="eyebrow">A POSSIBLE NEXT STEP</span><p>{message.steps[0]}</p></div>}
    {message.suggestion&&onSave&&<button className="secondary" onClick={()=>onSave(message)}><FolderPlus size={16}/>Review & save {message.suggestion.kind}</button>}
  </div>;
}
export function ChatMessages({messages,busy,onDisease,onSave}:{messages:Message[];busy:boolean;onDisease:(d:Disease)=>void;onSave?:(m:Message)=>void}){
  return <div className="chat-messages" aria-live="polite">{messages.map(m=>m.role==='user'?<div className="user-message" key={m.id}>{m.text}</div>:<Answer key={m.id} message={m} onDisease={onDisease} onSave={onSave} plain/>)}{busy&&<ThinkingIndicator/>}</div>;
}
