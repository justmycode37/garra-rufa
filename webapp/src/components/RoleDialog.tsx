'use client';

import { useState } from 'react';
import { LoaderCircle } from 'lucide-react';
import Dialog from './Dialog';
import { ChatGPTSignIn, RoleChoices } from './AuthDialog';
import type { Message, Role, User } from '@/lib/types';

export default function RoleDialog({ onClose, onSuccess, initialRole, user, conversation }: { onClose: () => void; onSuccess: (user: User) => void | Promise<void>; initialRole?: Role; user: User | null; conversation?: { messages: Message[] } }) {
  const [selected, setSelected] = useState<Role | null>(initialRole ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function choose(role: Role) {
    if (!user?.chatgpt) return;
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/auth/role', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      await onSuccess(data.user);
    } catch (error) { setError(error instanceof Error ? error.message : 'Unable to open your workspace. Please try again.'); }
    finally { setBusy(false); }
  }
  return <Dialog title="Choose your role" onClose={() => { if (!busy) onClose(); }}>
    <p className="role-dialog-intro">A workspace that fits your perspective.</p>
    <RoleChoices value={selected} onChange={setSelected} disabled={busy}/>
    {error && <p role="alert" className="form-error">{error}</p>}
    {user?.chatgpt ? <button type="button" className="primary full role-continue" disabled={!selected || busy} onClick={()=>{if(selected)void choose(selected);}}>{busy && <LoaderCircle size={16} className="spin"/>}{busy?'Opening workspace…':'Continue'}</button> : <ChatGPTSignIn role={selected} conversation={conversation}/>}
  </Dialog>;
}
