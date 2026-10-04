'use client';

import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { ArrowLeft, Maximize, Minus, Move, Plus, Rotate3D } from 'lucide-react';
import { AnatomyScene, type AnatomyFrame, type AnatomyGraph, type AnatomyManifest } from '@/lib/anatomy-scene';
import { atlasContextForTarget, atlasContextForDisease, discoverAtlas, type AtlasContext } from '@/lib/atlas-discovery';
import { diseases } from '@/lib/knowledge';
import type { Disease, Message } from '@/lib/types';
import { atlasRegions } from '@/lib/atlas-graph';
import { updateStreamingMessage, type OnAnswerText } from '@/lib/search-stream';
import { Composer, type ComposerContext } from './Chat';
import LandingChat from './LandingChat';
import AtlasKnowledgeGraph from './AtlasKnowledgeGraph';
import styles from './DiseaseAtlas.module.css';

const initialFrame: AnatomyFrame = { zoom: 1, labels: [], detailReady: false, detailError: false, region: '', rotated: false };

export default function DiseaseAtlas({ onDisease, composerContext, onAskWithContext, onCommunity }: {
  onDisease: (disease: Disease) => void;
  composerContext: ComposerContext;
  onAskWithContext: (query: string, history: Message[], onText: OnAnswerText, signal: AbortSignal) => Promise<Message>;
  onCommunity: (id: string) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<AnatomyScene | null>(null);
  const contextRef = useRef<AtlasContext | undefined>(undefined);
  const requestGeneration = useRef(0);
  const requestBusy = useRef(false);
  const activeRequest = useRef<AbortController | null>(null);
  const [frame, setFrame] = useState<AnatomyFrame>(initialFrame);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [pan, setPan] = useState(false);
  const [context, setContext] = useState<AtlasContext>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [chatOpen, setChatOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const graphRegion = frame.regionId && atlasRegions[frame.regionId] ? frame.regionId : '';
  const activeDisease = diseases.find(d => d.id === (context?.diseaseId ?? context?.diseaseIds[0]));

  function applyContext(next: AtlasContext | undefined, move = true) {
    if (next && !next.diseaseId) next = { ...next, diseaseId: next.diseaseIds[0] };
    contextRef.current = next;
    setContext(next);
    if (move) scene.current?.focus(next?.target ?? 'body', false);
  }

  function selectDisease(disease: Disease) {
    applyContext(atlasContextForDisease(disease, contextRef.current));
    ask(`Show research about ${disease.name}`);
  }

  async function ask(query: string) {
    if (requestBusy.current) return;
    const reply = discoverAtlas(query, contextRef.current);
    const history = messages;
    setMessages(previous => [...previous, { id: crypto.randomUUID(), role: 'user', text: query }]);
    if (reply.context) applyContext(reply.context);
    setChatOpen(true);
    requestBusy.current = true;
    setBusy(true);
    const generation = ++requestGeneration.current;
    const request = new AbortController();
    activeRequest.current = request;
    const assistantId = crypto.randomUUID();
    try {
      const scopedQuery = contextRef.current ? `${query}\n\nCurrent Atlas region: ${contextRef.current.label}.` : query;
      const answer = await onAskWithContext(scopedQuery, history, text => {
        if (generation === requestGeneration.current) setMessages(previous => updateStreamingMessage(previous, assistantId, text));
      }, request.signal);
      if (generation === requestGeneration.current) setMessages(previous => [...previous.filter(message => message.id !== assistantId), { ...answer, id: assistantId }]);
    } catch (error) {
      if (generation === requestGeneration.current) setMessages(previous => [...previous.filter(message => message.id !== assistantId), { id: assistantId, role: 'assistant', text: 'Your question could not be completed. Please try again.', warning: error instanceof Error ? error.message : 'Please try again.' }]);
    } finally {
      if (generation === requestGeneration.current) { activeRequest.current = null; requestBusy.current = false; setBusy(false); }
    }
  }

  useEffect(() => () => { requestGeneration.current++; activeRequest.current?.abort(); }, []);

  useEffect(() => {
    let active = true;
    const abort = new AbortController();
    setLoading(true); setError(''); setFrame(initialFrame); setPan(false);
    Promise.all([
      fetch('/models/anatomy/surface.json', { signal: abort.signal }).then(async response => { if (!response.ok) throw new Error(); return response.json() as Promise<AnatomyGraph>; }),
      fetch('/models/anatomy/manifest.json', { signal: abort.signal }).then(async response => { if (!response.ok) throw new Error(); return response.json() as Promise<AnatomyManifest>; }),
    ]).then(([graph, manifest]) => {
      if (!active || !canvas.current) return;
      scene.current = new AnatomyScene(canvas.current, graph, manifest,
        next => { if (active) setFrame(next); },
        () => { if (active) setError('The 3D view was interrupted. Reload the body to continue.'); },
        (id, title) => {
          if (!active) return;
          if (id === 'body') { applyContext(undefined, false); setChatOpen(false); return; }
          const next = atlasContextForTarget(id, title);
          applyContext(next, false);
          setChatOpen(false);
        });
      if (contextRef.current) scene.current.focus(contextRef.current.target, false);
      setLoading(false);
    }).catch(() => { if (active) { setLoading(false); setError('The 3D body could not be loaded. Please try again.'); } });
    return () => { active = false; abort.abort(); scene.current?.dispose(); scene.current = null; };
  }, [attempt]);


  useEffect(() => {
    if (graphRegion) applyContext(atlasContextForTarget(graphRegion, atlasRegions[graphRegion].label), false);
  }, [graphRegion]);

  function keyDown(event: KeyboardEvent<HTMLCanvasElement>) {
    const arrows: Record<string, [number, number]> = { ArrowLeft: [.12, 0], ArrowRight: [-.12, 0], ArrowUp: [0, -.1], ArrowDown: [0, .1] };
    if (arrows[event.key]) { event.preventDefault(); scene.current?.rotateBy(...arrows[event.key]); }
    else if (event.key === '+' || event.key === '=') { event.preventDefault(); scene.current?.zoomBy(1.3); }
    else if (event.key === '-') { event.preventDefault(); scene.current?.zoomBy(1 / 1.3); }
    else if (event.key === '0' || event.key === 'Escape') { event.preventDefault(); scene.current?.reset(); }
  }

  return <section className={styles.layout} aria-label="Interactive atlas">
      <div className={styles.atlas}>
        <canvas ref={canvas} className={styles.body} tabIndex={0} aria-label="3D human body" aria-describedby="atlas-instructions" onKeyDown={keyDown}/>
        {(loading || error) && <div className={styles.loading} role="status">{error || 'Preparing the body…'}{error && <button onClick={() => setAttempt(value => value + 1)}>Reload body</button>}</div>}
        {!loading && !error && <>
          {(frame.zoom > 1.05 || frame.rotated) && <div className={styles.breadcrumb}><button onClick={() => scene.current?.reset()}><ArrowLeft size={14}/>Whole body</button>{frame.region && <span>{frame.region}</span>}</div>}
          {(!frame.detailReady || frame.detailError) && <div className={styles.detailStatus} role="status">{frame.detailError ? <button onClick={() => setAttempt(value => value + 1)}>Retry anatomical detail</button> : 'Loading anatomical detail…'}</div>}
          <svg className={styles.connections} aria-hidden="true">
            {(!graphRegion ? frame.labels : []).map(label => {
              const x = label.side === 'left' ? label.x + label.width : label.x;
              const y = label.y + label.height / 2;
              return <g key={label.id} opacity={label.opacity}><path d={`M ${label.anchor.x} ${label.anchor.y} L ${x + (label.side === 'left' ? 18 : -18)} ${y} L ${x} ${y}`} fill="none" stroke="#000" strokeWidth=".8"/><circle cx={label.anchor.x} cy={label.anchor.y} r="2.5" fill="#000"/></g>;
            })}
          </svg>
          {(!graphRegion ? frame.labels : []).map(label => <button key={label.id} className={`${styles.annotation} ${label.overlapsGraph ? styles.overGraph : ''} ${label.side === 'left' ? styles.left : styles.right}`}
            style={{ transform: `translate3d(${label.x}px, ${label.y}px, 0)`, width: label.width, height: label.height, opacity: label.opacity, pointerEvents: label.interactive ? 'auto' : 'none' }}
            disabled={!label.interactive} aria-hidden={!label.interactive} tabIndex={label.interactive ? 0 : -1}
            aria-label={`Explore ${label.title}`}
            onClick={() => { const disease = diseases.find(d => d.id === label.diseaseId); if (disease) selectDisease(disease); else if (label.target) scene.current?.focus(label.target); }}><span className={styles.tagText}>{label.title}</span></button>)}
        </>}
        {!loading && !error && graphRegion && <AtlasKnowledgeGraph key={graphRegion} regionId={graphRegion} frame={frame} onAsk={ask}/>}
        <p className={styles.srOnly} id="atlas-instructions">Drag to rotate. Scroll or pinch to zoom. Shift-drag or use the move control to pan. Select an anatomy label or node to explore. Click a body part to reveal its records as labels connected to the anatomy. Select a disease to load genes, papers and specialist resources. Arrow keys rotate, plus and minus zoom, and 0 resets.</p>
        <div className={styles.footer}>
          <span className={styles.dragHint}>Drag to rotate · Scroll to zoom</span>
          <div className={styles.controls} role="group" aria-label="Atlas view controls">
            <button onClick={() => { scene.current?.setPanMode(!pan); setPan(!pan); }} aria-label={pan ? 'Switch to rotation' : 'Switch to panning'} title={pan ? 'Drag to move. Switch to rotation.' : 'Drag to rotate. Switch to panning.'} aria-pressed={pan}>{pan ? <Move size={17}/> : <Rotate3D size={19}/>}</button>
            <span/>
            <button onClick={() => scene.current?.zoomBy(1 / 1.3)} disabled={frame.zoom <= 1.001} aria-label="Zoom out"><Minus size={17}/></button>
            <output aria-label="Zoom level">{Math.round(frame.zoom * 100)}%</output>
            <button onClick={() => scene.current?.zoomBy(1.3)} disabled={frame.zoom >= 13.999} aria-label="Zoom in"><Plus size={17}/></button>
            <button onClick={() => scene.current?.reset()} aria-label="Reset body view" title="Reset body view"><Maximize size={16}/></button>
          </div>
        </div>
      </div>
    <div className={styles.promptDock}>
      {chatOpen && <LandingChat messages={messages} busy={busy} onDisease={onDisease} onCommunity={onCommunity} onClose={() => setChatOpen(false)} composer={null}/>}
      <Composer {...composerContext} onSubmit={ask} busy={busy} compact placeholder={graphRegion ? `Ask about ${atlasRegions[graphRegion].label}…` : context ? `Ask about ${activeDisease?.shortName ?? context.label}…` : 'Ask a question…'}/>
    </div>
  </section>;
}
