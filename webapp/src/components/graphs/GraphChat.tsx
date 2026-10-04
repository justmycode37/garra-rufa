'use client';
import { useEffect, useRef, useState } from 'react';
import { ChevronDown, MessageCircle, RotateCcw } from 'lucide-react';
import { Answer, Composer } from '@/components/Chat';
import ThinkingIndicator from '@/components/ThinkingIndicator';
import { useChatScroll } from '@/components/useChatScroll';
import { readAnimatedSearchResponse } from '@/lib/animated-search';
import { updateStreamingMessage } from '@/lib/search-stream';
import type { Message } from '@/lib/types';
import type { GraphRun } from './GraphsShell';
import styles from './Graphs.module.css';

const noop = () => {};

/** A chat below the graph about the open graph: /api/search answers it with the pipeline's
 * Markdown report of this graph (overview or evidence) as its context. */
export default function GraphChat({ run, view }: { run: GraphRun; view: 'present' | 'evidence' }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(true);
  const active = useRef<AbortController | null>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const expanded = open && (messages.length > 0 || busy);
  const follow = useChatScroll(transcript, expanded);
  const lastQuestion = messages.findLast(m => m.role === 'user')?.id;
  useEffect(() => { follow(); }, [lastQuestion, follow]);
  useEffect(() => () => active.current?.abort(), []);

  if (!(view === 'present' ? run.presentUrl : run.evidenceUrl)) return null;
  const subject = view === 'present' ? 'this overview' : 'this evidence graph';

  async function ask(query: string) {
    if (busy) return;
    const request = new AbortController();
    active.current = request;
    const assistantId = crypto.randomUUID();
    const history = messages;
    const pending: Message[] = [...history, { id: crypto.randomUUID(), role: 'user', text: query }];
    setMessages(pending); setBusy(true); setOpen(true);
    try {
      const response = await fetch('/api/search', { method: 'POST', signal: request.signal,
        headers: { 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
        body: JSON.stringify({ query, surface: 'landing', graph: { id: run.id, view },
          history: history.slice(-8).map(({ id, role, text }) => ({ id, role, text: text.slice(0, 8000) })) }) });
      const data = await readAnimatedSearchResponse(response, text => setMessages(current => updateStreamingMessage(current, assistantId, text)), request.signal);
      const answer = data.messages.at(-1);
      if (!answer || answer.role !== 'assistant') throw new Error('The assistant returned no answer. Please try again.');
      setMessages([...pending, { ...answer, id: assistantId }].slice(-40));
    } catch (error) {
      if (request.signal.aborted) return;
      setMessages([...pending, { id: assistantId, role: 'assistant', text: 'Your question could not be answered.', mode: 'database',
        warning: error instanceof Error ? error.message : 'Please try again.' }]);
    } finally {
      if (active.current === request) { active.current = null; setBusy(false); }
    }
  }
  function restart() { active.current?.abort(); active.current = null; setBusy(false); setMessages([]); }

  return <section className={styles.chat} aria-label={`Ask about ${run.label}`}>
    {expanded && <div className={styles.chatLog}>
      <div className={styles.chatHead}>
        <span className="eyebrow"><MessageCircle size={13}/> Chat about {subject}</span>
        <button className={styles.iconButton} onClick={restart} aria-label="Start a new conversation" title="New conversation"><RotateCcw size={15}/></button>
        <button className={styles.iconButton} onClick={() => setOpen(false)} aria-label="Hide the conversation" title="Hide"><ChevronDown size={17}/></button>
      </div>
      <div className={styles.chatTranscript} ref={transcript} role="log" aria-live="polite" aria-relevant="additions text" tabIndex={0}>
        {messages.map(m => m.role === 'user'
          ? <div className="user-message" key={m.id}>{m.text}</div>
          : <Answer key={m.id} message={m} onDisease={noop} plain/>)}
        {busy && !messages.at(-1)?.streaming && <ThinkingIndicator/>}
      </div>
    </div>}
    <div className={styles.chatComposer}>
      {!expanded && messages.length > 0 && <button className={styles.chatShow} onClick={() => setOpen(true)}><MessageCircle size={14}/>Show the conversation ({messages.filter(m => m.role === 'user').length})</button>}
      <Composer onSubmit={ask} busy={busy} compact voiceEnabled={false} contextLabel={`Ask about ${subject}`}
        placeholder={view === 'present' ? `Ask about ${run.label}: symptoms, genes, treatments, trials…` : `Ask about the evidence for ${run.label}: candidates, papers, gaps…`}/>
    </div>
  </section>;
}
