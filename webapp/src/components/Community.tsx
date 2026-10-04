'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowUpRight, LoaderCircle, Plus, Search, Send, Trash2, Users } from 'lucide-react';
import type { ConditionOption, User } from '@/lib/types';
import { diseases } from '@/lib/knowledge';
import ConditionPicker from './ConditionPicker';
import Dialog from './Dialog';
import styles from './Community.module.css';

type Profile = { alias: string; diseaseId: string; bio: string };
type Post = { id: string; conditionId: string; author: string; content: string; createdAt: string; own: boolean };
type Project = { id: string; title: string; content: string; author: string; diseaseId?: string };
type CommunityData = {
  conditions: ConditionOption[];
  condition?: ConditionOption | null;
  profiles?: Profile[];
  profile?: Profile | null;
  members: (Profile & { id: string })[];
  projects: Project[];
  posts: Post[];
};
const empty: CommunityData = { conditions: [], members: [], projects: [], posts: [] };

export default function Community({ user, onAsk }: { user: User; onAsk: (query: string) => void }) {
  const [data, setData] = useState<CommunityData>(empty);
  const [conditionId, setConditionId] = useState('');
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [joining, setJoining] = useState(false);
  const [joinId, setJoinId] = useState('');
  const [alias, setAlias] = useState('');
  const [bio, setBio] = useState('');
  const joinSelection = useRef('');
  const [formError, setFormError] = useState('');
  const [busy, setBusy] = useState(false);
  const [content, setContent] = useState('');
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [deletePost, setDeletePost] = useState<Post | null>(null);
  const [leaving, setLeaving] = useState(false);
  const generation = useRef(0);

  const load = useCallback(async (id: string) => {
    const request = ++generation.current;
    setLoading(true); setError('');
    try {
      const response = await fetch(`/api/community${id ? `?conditionId=${encodeURIComponent(id)}` : ''}`);
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Community could not load.');
      if (request === generation.current) setData(result);
    } catch (e) {
      if (request === generation.current) setError(e instanceof Error ? e.message : 'Community could not load.');
    } finally { if (request === generation.current) setLoading(false); }
  }, []);
  useEffect(() => { void load(conditionId); return () => { generation.current++; }; }, [conditionId, load]);

  const condition = conditionId ? data.conditions.find(c => c.id === conditionId) : undefined;
  const profile = conditionId ? data.profiles?.find(p => p.diseaseId === conditionId) || (data.profile?.diseaseId === conditionId ? data.profile : null) : null;
  const organization = diseases.find(d => d.id === conditionId)?.organization;
  const open = (id: string) => { setConditionId(id); setContent(''); setError(''); };
  const editingProfile = data.profiles?.find(p => p.diseaseId === joinId) || null;
  const openJoin = () => {
    setFormError(''); setJoinId(conditionId); joinSelection.current = conditionId;
    setAlias(profile?.alias || user.name.split(' ')[0]); setBio(profile?.bio || ''); setJoining(true);
  };
  const changeJoinCondition = useCallback(({ diseaseId }: { diseaseId: string; conditionName: string }) => {
    if (joinSelection.current === diseaseId) return;
    joinSelection.current = diseaseId; setJoinId(diseaseId);
    const existing = data.profiles?.find(p => p.diseaseId === diseaseId);
    setAlias(existing?.alias || user.name.split(' ')[0]); setBio(existing?.bio || '');
  }, [data.profiles, user.name]);

  async function join(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setFormError('');
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch('/api/community', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        action: 'profile', alias: form.get('alias'), diseaseId: form.get('diseaseId') || undefined,
        conditionName: form.get('conditionName') || undefined, bio: form.get('bio'),
      }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not save your profile.');
      const id = result.profile?.diseaseId || result.condition?.id;
      if (!id) throw new Error('Could not open this community. Please try again.');
      setJoining(false); setConditionId(id); setContent(''); await load(id);
    } catch (e) { setFormError(e instanceof Error ? e.message : 'Please try again.'); }
    finally { setBusy(false); }
  }

  async function post(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!content.trim() || !conditionId) return;
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/community', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'post', conditionId, content: content.trim() }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not share this post.');
      setContent(''); await load(conditionId);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not share this post.'); }
    finally { setBusy(false); }
  }

  async function remove(payload: { postId: string } | { conditionId: string }) {
    setBusy(true); setFormError('');
    try {
      const response = await fetch('/api/community', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not update the community.');
      setDeletePost(null); setLeaving(false); setJoining(false); await load(conditionId);
    } catch (e) { setFormError(e instanceof Error ? e.message : 'Please try again.'); }
    finally { setBusy(false); }
  }

  const filtered = data.conditions.filter(c => [c.name, ...(c.aliases || [])].join(' ').toLowerCase().includes(filter.trim().toLowerCase()));

  return <div className={styles.community}>
    {conditionId && <button className={`text-button ${styles.back}`} disabled={busy} onClick={() => open('')}><ArrowLeft size={14}/>All communities</button>}
    <div className="section-heading">
      <div><h1>{condition?.name || (conditionId ? 'Community' : 'Communities')}</h1>{condition && <p>{condition.memberCount} {condition.memberCount === 1 ? 'member' : 'members'}</p>}</div>
      <button className="secondary" onClick={openJoin} disabled={busy || (loading && !!conditionId)}>{conditionId ? profile ? 'Your profile' : 'Join community' : 'Join or create'}{conditionId ? <Users size={16}/> : <Plus size={16}/>}</button>
    </div>
    {error && <div className="form-error" role="alert">{error}<button className="text-button" onClick={() => load(conditionId)}>Try again</button></div>}
    {!conditionId ? <>
      <div className={`search-box ${styles.search}`}><Search size={17}/><input aria-label="Find a community" value={filter} onChange={e => setFilter(e.target.value)} placeholder="Find your condition…"/></div>
      {loading ? <div className={styles.empty} role="status"><LoaderCircle className="spin" size={19}/>Loading communities…</div> : <div className={styles.directory}>
        {filtered.map(c => <button className={styles.condition} key={c.id} onClick={() => open(c.id)}>
          <span className={styles.conditionIcon}><Users size={19} strokeWidth={1.5}/></span>
          <span><b>{c.name}</b><small>{c.memberCount} {c.memberCount === 1 ? 'member' : 'members'} · {c.postCount} {c.postCount === 1 ? 'post' : 'posts'}{c.joined || data.profiles?.some(p => p.diseaseId === c.id) ? ' · Joined' : ''}</small></span>
          <ArrowUpRight size={16}/>
        </button>)}
        {!filtered.length && <div className={styles.empty}><p>No community found for “{filter}”.</p><button className="text-button" onClick={openJoin}>Create a community<Plus size={14}/></button></div>}
      </div>}
    </> : loading ? <div className={styles.empty} role="status"><LoaderCircle className="spin" size={19}/>Loading community…</div> : condition && data.condition?.id === conditionId ? <div className={styles.layout}>
      <section aria-labelledby="conversation-title">
        <h2 id="conversation-title" className={styles.sectionTitle}>Conversations</h2>
        {profile ? <form className={styles.composer} onSubmit={post}>
          <label htmlFor="community-post">Share with {condition.name}</label>
          <textarea id="community-post" value={content} onChange={e => setContent(e.target.value)} maxLength={3000} rows={3} placeholder="Share an experience, ask a question, or start a conversation." required disabled={busy}/>
          <div><small>Visible to people using the platform.</small><button className="primary" disabled={busy || !content.trim()}>{busy ? <LoaderCircle size={14} className="spin"/> : <Send size={14}/>}Share post</button></div>
        </form> : <div className={styles.joinPrompt}><p>Connect with people who share this experience.</p><button className="text-button" onClick={openJoin}>Join the conversation<ArrowUpRight size={14}/></button></div>}
        <div className={styles.posts}>
          {data.posts.map(p => <article className={styles.post} key={p.id}>
            <header><span className={styles.avatar}>{p.author.slice(0, 1).toUpperCase()}</span><div><b>{p.author}</b><time dateTime={p.createdAt}>{new Date(p.createdAt).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}</time></div>{p.own && <button className="icon-button" aria-label="Delete your post" onClick={() => { setFormError(''); setDeletePost(p); }}><Trash2 size={14}/></button>}</header>
            <p>{p.content}</p>
          </article>)}
          {!data.posts.length && <p className={styles.emptyCopy}>No conversations yet. There’s room for yours.</p>}
        </div>
        {data.projects.length > 0 && <section className={styles.research} aria-labelledby="community-research-title"><h2 id="community-research-title" className={styles.sectionTitle}>Shared research</h2><small>Community submissions · not peer reviewed</small>{data.projects.map(p => <button className={styles.project} key={p.id} onClick={() => setSelectedProject(p)}><span><b>{p.title}</b><small>{p.author}</small></span><ArrowUpRight size={15}/></button>)}</section>}
      </section>
      <aside className={styles.people} aria-labelledby="community-members-title"><h2 id="community-members-title" className={styles.sectionTitle}>People</h2>
        {data.members.length ? data.members.map(m => <article className={styles.member} key={m.id || m.alias}><span className={styles.avatar}>{m.alias.slice(0, 1).toUpperCase()}</span><div><h3>{m.alias}</h3>{m.bio && <p>{m.bio}</p>}</div></article>) : <p className={styles.emptyCopy}>Be the first to introduce yourself.</p>}
        {organization && <a className={styles.organization} href={organization.url} target="_blank" rel="noreferrer">{organization.name}<ArrowUpRight size={14}/></a>}
        <p className={styles.privacy}>Your documents and saved notes stay private. Only what you choose to share appears here.</p>
      </aside>
    </div> : null}

    {joining && <Dialog title={editingProfile ? 'Your community profile' : 'Join a community'} onClose={() => { if (!busy) setJoining(false); }}>
      <p className={styles.disclosure}>Your display name and introduction will be visible in the community.</p>
      <form className="auth-form" onSubmit={join}>
        <ConditionPicker defaultValue={conditionId || undefined} defaultName={!conditionId ? filter : undefined} label="Condition" required disabled={busy} onValueChange={changeJoinCondition} createHint="Saving creates a community for this condition."/>
        <label>Display name<input name="alias" value={alias} onChange={e => setAlias(e.target.value)} required minLength={2} maxLength={50} disabled={busy}/></label>
        <label>Introduction<textarea name="bio" value={bio} onChange={e => setBio(e.target.value)} rows={3} maxLength={300} placeholder="A little about you, on your own terms." disabled={busy}/></label>
        <button className="primary" disabled={busy}>{busy ? <LoaderCircle size={15} className="spin"/> : <Users size={15}/>} {editingProfile ? 'Save profile' : 'Join community'}</button>
        {profile && joinId === conditionId && <button className="text-button" type="button" disabled={busy} onClick={() => { setJoining(false); setFormError(''); setLeaving(true); }}>Leave this community</button>}
      </form>{formError && <p className="form-error" role="alert">{formError}</p>}
    </Dialog>}
    {deletePost && <Dialog title="Delete your post?" onClose={() => { if (!busy) setDeletePost(null); }}><p className={styles.disclosure}>This removes your post from the community.</p><div className="button-row"><button className="secondary" disabled={busy} onClick={() => setDeletePost(null)}>Keep post</button><button className="primary" disabled={busy} onClick={() => remove({ postId: deletePost.id })}>Delete post</button></div>{formError && <p className="form-error" role="alert">{formError}</p>}</Dialog>}
    {leaving && <Dialog title="Leave this community?" onClose={() => { if (!busy) setLeaving(false); }}><p className={styles.disclosure}>Your profile will be removed from this community. Your shared posts stay visible; you can delete them individually.</p><div className="button-row"><button className="secondary" disabled={busy} onClick={() => setLeaving(false)}>Stay</button><button className="primary" disabled={busy} onClick={() => remove({ conditionId })}>Leave community</button></div>{formError && <p className="form-error" role="alert">{formError}</p>}</Dialog>}
    {selectedProject && <Dialog title={selectedProject.title} onClose={() => setSelectedProject(null)} wide><span className="source-tag">{selectedProject.author} · Not peer reviewed</span><p className="pre-wrap">{selectedProject.content}</p><button className="primary" onClick={() => { onAsk(`Review this community research summary, distinguishing its claims from established evidence: ${selectedProject.title}\n${selectedProject.content.slice(0, 12000)}`); setSelectedProject(null); }}>Explore with assistant<ArrowUpRight size={16}/></button></Dialog>}
  </div>;
}
