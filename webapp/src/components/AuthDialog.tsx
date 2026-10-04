'use client';
import { useEffect, useState } from 'react';
import { Check, Microscope, Stethoscope, Heart, LoaderCircle, LogOut } from 'lucide-react';
import Dialog from './Dialog';
import type { Message, Role, User } from '@/lib/types';

export const roleMeta={researcher:{label:'Researcher',plural:'Researchers',icon:Microscope,color:'lilac',description:'Follow a question. Find a connection.',space:'Research space'},doctor:{label:'Doctor',plural:'Doctors',icon:Stethoscope,color:'sea',description:'Bring the evidence into focus.',space:'Clinical space'},patient:{label:'Patient',plural:'Patients & families',icon:Heart,color:'peach',description:'Understand more. Feel less alone.',space:'My health space'}};

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

export const authConversationKey = 'garra:auth-conversation';
export function AccountSignIn({ role, onSuccess, conversation, initialError = '', initialMode = 'login' }: { role: Role | null; onSuccess: (user: User) => void | Promise<void>; conversation?: { messages: Message[] }; initialError?: string; initialMode?: 'login'|'signup'|'password' }) {
  const [mode, setMode] = useState<'login'|'signup'|'reset'|'password'>(initialMode);
  const [google, setGoogle] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(initialError);
  const [notice, setNotice] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  useEffect(() => { const abort = new AbortController(); fetch('/api/auth', { signal: abort.signal }).then(r=>r.json()).then(d=>setGoogle(d.googleEnabled === true)).catch(()=>{}); return ()=>abort.abort(); }, []);
  async function submit(action: typeof mode | 'google') {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const response = await fetch('/api/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, email: email.trim(), password, name: name.trim(), role }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Please try again.');
      if (data.url) {
        try { if (conversation) sessionStorage.setItem(authConversationKey, JSON.stringify(conversation)); else sessionStorage.removeItem(authConversationKey); } catch {}
        window.location.assign(data.url); return;
      }
      setPassword('');
      if (data.user) { await onSuccess(data.user); return; }
      setNotice(data.message || 'Check your email for the next step.');
    } catch (error) { setError(error instanceof Error ? error.message : 'Please try again.'); }
    finally { setBusy(false); }
  }
  function switchMode(next: typeof mode) { setMode(next); setError(''); setNotice(''); setPassword(''); }
  return <div className="account-sign-in">
    {(mode === 'login' || mode === 'signup') && <>
      <button className="google-sign-in" type="button" aria-label="Sign in with Google" onClick={()=>void submit('google')} disabled={!google || busy}><img src="/brand/google-sign-in.svg" width="180" height="40" alt=""/></button>
      {!google && <p className="auth-small">Google sign-in is coming soon. You can use email below.</p>}
      <div className="auth-divider"><span>or use email</span></div>
    </>}
    <form className="auth-form" onSubmit={event=>{event.preventDefault();void submit(mode);}}>
      {mode==='signup'&&<label>Your name<input autoComplete="name" value={name} onChange={e=>setName(e.target.value)} required maxLength={100} disabled={busy}/></label>}
      {mode!=='password'&&<label>Email<input type="email" autoComplete="email" value={email} onChange={e=>setEmail(e.target.value)} required maxLength={254} disabled={busy}/></label>}
      {mode!=='reset'&&<label>{mode==='password'?'New password':'Password'}<input type="password" autoComplete={mode==='login'?'current-password':'new-password'} value={password} onChange={e=>setPassword(e.target.value)} minLength={mode==='login'?1:10} maxLength={128} required disabled={busy}/>{mode!=='login'&&<small>At least 10 characters.</small>}</label>}
      {mode==='reset'&&<p className="auth-small">We’ll send you a link to choose a new password.</p>}
      {error&&<p className="form-error" role="alert">{error}</p>}
      {notice&&<p className="service-notice" role="status">{notice}</p>}
      <button className="primary full" type="submit" disabled={busy}>{busy?<><LoaderCircle size={16} className="spin"/>Please wait…</>:mode==='signup'?'Create account':mode==='reset'?'Send reset link':mode==='password'?'Save new password':'Sign in'}</button>
    </form>
    {(mode==='login'||mode==='signup')&&<p className="auth-switch">{mode==='login'?"Don’t have an account? ":'Already have an account? '}<button type="button" disabled={busy} onClick={()=>switchMode(mode==='login'?'signup':'login')}>{mode==='login'?'Sign up here':'Sign in here'}</button></p>}
    {mode==='login'&&<button className="text-button auth-reset" type="button" disabled={busy} onClick={()=>switchMode('reset')}>Forgot password?</button>}
    {mode==='reset'&&<button className="text-button auth-reset" type="button" onClick={()=>switchMode('login')}>Back to sign in</button>}
  </div>;
}

export default function AuthDialog({ onClose, onSuccess, user, onSignOut, conversation, initialRole, initialError, recovery = false, initialMode = 'login' }: { onClose: () => void; onSuccess: (user: User) => void | Promise<void>; user: User | null; onSignOut: () => Promise<void>; conversation?: { messages: Message[] }; initialRole?: Role; initialError?: string; recovery?: boolean; initialMode?: 'login'|'signup' }) {
  const [role, setRole] = useState<Role | null>(user?.role || initialRole || null);
  const [step, setStep] = useState<'role'|'account'>('role');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if(!user&&!recovery&&step==='role')return <Dialog title="Choose your role" onClose={onClose} className="account-dialog">
    <p className="role-dialog-intro">A workspace that fits your perspective.</p>
    <RoleChoices value={role} onChange={setRole}/>
    <button type="button" className="primary full role-continue" disabled={!role} onClick={()=>setStep('account')}>Continue</button>
  </Dialog>;
  return <Dialog title={recovery?'Choose a new password':user?'Your account':'Welcome to Garra Rufa'} onClose={onClose} className="account-dialog">
    {user&&!recovery?<div className="chatgpt-active-account"><b>{user.name}</b><span>{user.email}</span></div>:<>
      <AccountSignIn role={role} onSuccess={onSuccess} conversation={conversation} initialError={initialError} initialMode={recovery?'password':initialMode}/>
      {!recovery&&<button className="text-button auth-back" type="button" onClick={()=>setStep('role')}>Back to role selection</button>}
    </>}
    {error&&<p className="form-error" role="alert">{error}</p>}
    {user&&!recovery&&<button className="primary full chatgpt-sign-out" disabled={busy} onClick={async()=>{setBusy(true);try{await onSignOut();}catch(error){setError(error instanceof Error?error.message:'Sign-out failed.');}finally{setBusy(false);}}}><LogOut size={15}/>{busy?'Signing out…':'Sign out'}</button>}
  </Dialog>;
}
