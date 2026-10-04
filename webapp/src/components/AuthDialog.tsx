'use client';
import { useEffect, useState } from 'react';
import { Check, Microscope, Stethoscope, Heart, LoaderCircle, LogOut } from 'lucide-react';
import Dialog from './Dialog';
import type { Message, Role, User } from '@/lib/types';

export const roleMeta={researcher:{label:'Researcher',plural:'Researchers',icon:Microscope,color:'lilac',description:'Follow a question. Find a connection.',space:'Research space'},doctor:{label:'Doctor',plural:'Doctors',icon:Stethoscope,color:'sea',description:'Bring the evidence into focus.',space:'Clinical space'},patient:{label:'Patient',plural:'Patients & families',icon:Heart,color:'peach',description:'Understand more. Feel less alone.',space:'My health space'}};
type SavedAccount = { id: string; name: string; email: string; label: string };

export function RoleChoices({ value, onChange, disabled=false }: { value: Role | null; onChange: (role: Role) => void; disabled?: boolean }) {
  return <div className="entry-roles" role="radiogroup" aria-label="Your role">{(['researcher', 'doctor', 'patient'] as Role[]).map(role => {
    const meta=roleMeta[role], Icon=meta.icon;
    return <label key={role} className={`entry-role ${meta.color}${disabled?' is-busy':''}`}>
      <input className="entry-role-input" type="radio" name="role" value={role} checked={value===role} onChange={()=>onChange(role)} disabled={disabled}/>
      <span className="entry-role-icon" aria-hidden="true"><Icon size={20} strokeWidth={1.5}/></span>
      <span className="entry-role-copy"><b>{meta.label}</b><small>{role==='researcher'?'Explore papers and build research.':role==='doctor'?'Connect patient context with evidence.':'Understand your health and find support.'}</small></span>
      <span className="entry-role-check" aria-hidden="true"><Check size={14} strokeWidth={2.5}/></span>
    </label>;
  })}</div>;
}

export function ChatGPTSignIn({ role, conversation, initialError = '' }: { role: Role | null; conversation?: { messages: Message[] }; initialError?: string }) {
  const [accounts, setAccounts] = useState<SavedAccount[]>([]);
  const [selected, setSelected] = useState('');
  const [available, setAvailable] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(initialError);
  useEffect(() => {
    const abort = new AbortController();
    fetch('/api/auth/chatgpt', { signal: abort.signal }).then(async response => {
      if (!response.ok) throw new Error('Sign-in options could not be loaded. Please reopen this dialog.');
      const data = await response.json(); setAccounts(data.accounts); setSelected(data.accounts[0]?.id || ''); setAvailable(data.available);
    }).catch(error => { if (!abort.signal.aborted) setError(error instanceof Error ? error.message : 'Sign-in could not be loaded.'); });
    return () => abort.abort();
  }, []);
  async function signIn() {
    if (!role || busy) return;
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/auth/chatgpt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role, accountId: selected || undefined, conversation }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to start ChatGPT sign-in.');
      window.location.assign(data.url);
    } catch (error) { setError(error instanceof Error ? error.message : 'Unable to start ChatGPT sign-in.'); setBusy(false); }
  }
  return <div className="chatgpt-sign-in">
    {!!accounts.length && <label className="chatgpt-account-select">ChatGPT account<select value={selected} onChange={event => setSelected(event.target.value)} disabled={busy}>
      <option value="">Use another ChatGPT account</option>
      {accounts.map(account => <option key={account.id} value={account.id}>{account.label}</option>)}
    </select></label>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {available === false && <p className="service-notice">Open Garra Rufa at <b>http://127.0.0.1:3000</b> on the computer running the app to connect ChatGPT. Hosted sign-in is not enabled.</p>}
    <button className="primary full chatgpt-continue" type="button" onClick={signIn} disabled={!role || busy || available !== true}>
      {busy && <LoaderCircle size={17} className="spin"/>}{busy ? 'Opening ChatGPT…' : 'Continue with ChatGPT'}
    </button>
    <p className="chatgpt-sign-in-note">AI answers use your ChatGPT plan and its limits. No API key needed. You choose what to share.</p>
  </div>;
}

export default function AuthDialog({ onClose, user, onSignOut, conversation, initialRole = 'researcher', initialError }: { onClose: () => void; user: User | null; onSignOut: () => Promise<void>; conversation?: { messages: Message[] }; initialRole?: Role; initialError?: string }) {
  const [role, setRole] = useState<Role>(user?.role || initialRole);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <Dialog title={user?.chatgpt ? 'Your ChatGPT account' : 'Choose your role'} onClose={onClose}>
    {user?.chatgpt ? <div className="chatgpt-active-account"><b>{user.name}</b><span>{user.email}</span></div> : <>
      <p className="role-dialog-intro">A workspace that fits your perspective.</p>
      <RoleChoices value={role} onChange={setRole}/>
    </>}
    {!user?.chatgpt&&<ChatGPTSignIn role={role} conversation={conversation} initialError={initialError}/>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {user && <button className="primary full chatgpt-sign-out" disabled={busy} onClick={async () => { setBusy(true); try { await onSignOut(); } catch (error) { setError(error instanceof Error ? error.message : 'Sign-out failed.'); } finally { setBusy(false); } }}><LogOut size={15}/>{busy ? 'Signing out…' : 'Sign out'}</button>}
  </Dialog>;
}
