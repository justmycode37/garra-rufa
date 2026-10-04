'use client';
import { useEffect, useRef, useState } from 'react';
import { Maximize2, Minus, Plus } from 'lucide-react';
import { ForceCanvas, type ForceCanvasOptions } from '@/lib/force-canvas';
import styles from './Graphs.module.css';

/** A ForceCanvas on a canvas element; callbacks always see the latest render's closures. */
export function useForceCanvas(options: ForceCanvasOptions) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [engine, setEngine] = useState<ForceCanvas | null>(null);
  const latest = useRef(options);
  latest.current = options;
  useEffect(() => {
    const font = getComputedStyle(document.body).fontFamily || 'sans-serif';
    const fc = new ForceCanvas(canvas.current!, {
      ...latest.current, font,
      onClick: hit => latest.current.onClick?.(hit),
      onHover: hit => latest.current.onHover?.(hit),
      showLinkLabels: () => latest.current.showLinkLabels?.() ?? false,
    });
    setEngine(fc);
    return () => fc.destroy();
  }, []);
  return { canvas, engine };
}

export function ZoomControls({ engine }: { engine: ForceCanvas | null }) {
  return <div className={styles.zoom} role="group" aria-label="Graph zoom">
    <button onClick={() => engine?.zoomBy(1.4)} aria-label="Zoom in"><Plus size={15}/></button>
    <button onClick={() => engine?.zoomBy(1 / 1.4)} aria-label="Zoom out"><Minus size={15}/></button>
    <button onClick={() => engine?.fit()} aria-label="Fit graph to view"><Maximize2 size={14}/></button>
  </div>;
}
