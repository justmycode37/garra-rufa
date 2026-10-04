'use client';

import { createContext, useContext, useRef, useState, type ReactNode } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';

const ConnectionScene = dynamic(() => import('./ConnectionScene'), { ssr: false });
export type ConnectionDestination = 'body' | 'about';
export type SceneControl = { enterAbout: () => boolean; enterExplore: () => boolean };
const Context = createContext<SceneControl>({ enterAbout: () => false, enterExplore: () => false });

export default function ConnectionExperience({ children }: { children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [phase, setPhase] = useState<'idle' | 'leaving' | 'arriving' | 'returning' | 'settling'>('idle');
  const control = useRef<SceneControl | null>(null);
  const [ready, setReady] = useState(false);
  const [fallback, setFallback] = useState(false);
  // The layout owns the renderer, so route navigation cannot destroy the arm.
  const route = useRef(path);
  route.current = path;
  return <Context.Provider value={{ enterAbout: () => {
    if (phase !== 'idle') return true;
    if (!ready || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false;
    if (!control.current?.enterAbout()) return false;
    router.prefetch('/about');
    setPhase('leaving');
    return true;
  }, enterExplore: () => {
    if (phase !== 'idle') return true;
    if (!ready || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false;
    if (!control.current?.enterExplore()) return false;
    router.prefetch('/?view=explore');
    setPhase('returning');
    return true;
  } }}>
    <div className="connection-experience" data-connection-phase={phase} data-connection-fallback={fallback}>
      {children}
      {path !== '/demo' && <ConnectionScene control={control} route={route} onReady={value => { setReady(value); if (value) setFallback(false); }}
        onUnavailable={() => { setReady(false); setFallback(true); setPhase('idle'); }}
        onNavigate={destination => router.push(destination === 'about' ? '/about' : '/?view=explore', { scroll: false })}
        onArrive={destination => setPhase(destination === 'about' ? 'arriving' : 'settling')}
        onComplete={destination => {
          setPhase('idle');
          const target = destination === 'about' ? document.getElementById('mission-title') : document.querySelector<HTMLElement>('.about-link');
          target?.focus({ preventScroll: true });
        }}/>}
    </div>
  </Context.Provider>;
}

export function AboutLink() {
  const { enterAbout } = useContext(Context);
  return <Link className="text-button about-link" href="/about" onNavigate={event => {
    if (enterAbout()) event.preventDefault();
  }}>About</Link>;
}

export function ExploreLink({ children, className, 'aria-label': label }: { children: ReactNode; className?: string; 'aria-label'?: string }) {
  const { enterExplore } = useContext(Context);
  return <Link className={className} aria-label={label} href="/?view=explore" onNavigate={event => {
    if (enterExplore()) event.preventDefault();
  }}>{children}</Link>;
}
