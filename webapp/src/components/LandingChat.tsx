'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, Check, Copy, X } from 'lucide-react';
import { Answer } from './Chat';
import ThinkingIndicator from './ThinkingIndicator';
import type { Disease, Message } from '@/lib/types';
import styles from './LandingChat.module.css';

function CopyAnswer({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timeout = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timeout);
  }, [copied]);
  return <>
    <button className={styles.copy} type="button" aria-label={copied ? 'Answer copied' : 'Copy answer'} title={copied ? 'Copied' : 'Copy answer'} onClick={async () => {
      try { await navigator.clipboard.writeText(text); setCopied(true); setError(false); }
      catch { setError(true); }
    }}>{copied ? <Check size={16}/> : <Copy size={16}/>}</button>
    {error && <span className={styles.copyError} role="status">Select the answer to copy it.</span>}
  </>;
}

export default function LandingChat({ messages, busy, composer, onDisease, onClose, onContinue }: {
  messages: Message[];
  busy: boolean;
  composer: ReactNode;
  onDisease: (disease: Disease) => void;
  onClose: () => void;
  onContinue?: () => void;
}) {
  const expanded = messages.length > 0 || busy;
  const transcript = useRef<HTMLDivElement>(null);
  const latestQuestion = useRef<HTMLDivElement>(null);
  // Keep the beginning of the newest answer in view, including for long replies.
  useEffect(() => {
    const container = transcript.current;
    const question = latestQuestion.current;
    if (container && question) container.scrollTo({ top: question.offsetTop - 20 });
  }, [messages, busy]);
  const lastQuestionIndex = messages.findLastIndex(message => message.role === 'user');
  return <section className={`${styles.panel} ${expanded ? styles.expanded : ''}`} aria-label="Ask Garra Rufa">
    <div className={styles.expansion} inert={!expanded} aria-hidden={!expanded}>
      <div className={styles.conversation}>
        <div className={styles.toolbar}>
          <button className="icon-button" type="button" aria-label="Close conversation" onClick={onClose}><X size={17}/></button>
        </div>
        <div className={styles.transcript} ref={transcript} role="log" aria-label="Conversation" aria-live="polite" aria-relevant="additions text" tabIndex={expanded ? 0 : -1}>
          {messages.map((message, index) => message.role === 'user'
            ? <div className={styles.question} key={message.id} ref={index === lastQuestionIndex ? latestQuestion : undefined}><span className={styles.srOnly}>You: </span>{message.text}</div>
            : <div className={styles.response} key={message.id}>
              <span className={styles.srOnly}>Garra Rufa: </span>
              <Answer message={message} onDisease={onDisease} plain/>
              <div className={styles.actions}><CopyAnswer text={message.text}/></div>
              {onContinue && !busy && index === messages.length - 1 && <button className={styles.continue} type="button" onClick={onContinue}>Continue in workspace<ArrowRight size={16}/></button>}
            </div>)}
          {busy && <ThinkingIndicator/>}
        </div>
      </div>
    </div>
    {composer}
  </section>;
}
