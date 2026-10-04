'use client';

import { useEffect, useRef, type CSSProperties } from 'react';
import styles from './FishSchool.module.css';

const fish = [
  { x: 32, y: 0, size: .62 },
  { x: 3, y: -16, size: .54 },
  { x: -3, y: 17, size: .58 },
  { x: -32, y: -12, size: .5 },
  { x: -39, y: 15, size: .53 },
];

export default function FishSchool() {
  const scene = useRef<SVGSVGElement>(null);
  const school = useRef<SVGGElement>(null);

  useEffect(() => {
    const svg = scene.current;
    const group = school.current;
    if (!svg || !group) return;

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let width = svg.clientWidth;
    let height = svg.clientHeight;
    let visible = false;
    let elapsed = 0;
    let previousTime: number | null = null;
    let frame = 0;

    const draw = () => {
      if (reducedMotion.matches) {
        group.setAttribute('transform', `translate(${width / 2} ${height / 2})`);
        return;
      }
      // One shared route keeps the five fish together, including through turns.
      const phase = elapsed * .00028;
      const radius = Math.max(0, width / 2 - 85);
      const x = width / 2 + radius * Math.sin(phase);
      const y = height / 2 + 34 * Math.sin(phase * 2);
      const heading = Math.atan2(68 * Math.cos(phase * 2), radius * Math.cos(phase)) * 180 / Math.PI;
      group.setAttribute('transform', `translate(${x} ${y}) rotate(${heading})`);
    };

    const tick = (time: number) => {
      if (previousTime !== null) elapsed += Math.min(time - previousTime, 64);
      previousTime = time;
      draw();
      frame = requestAnimationFrame(tick);
    };

    const syncAnimation = () => {
      cancelAnimationFrame(frame);
      previousTime = null;
      const active = visible && document.visibilityState === 'visible';
      svg.dataset.visible = String(active);
      draw();
      if (active && !reducedMotion.matches) frame = requestAnimationFrame(tick);
    };

    const resize = new ResizeObserver(() => {
      width = svg.clientWidth;
      height = svg.clientHeight;
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      draw();
    });
    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      syncAnimation();
    }, { threshold: .1 });

    resize.observe(svg);
    observer.observe(svg);
    reducedMotion.addEventListener('change', syncAnimation);
    document.addEventListener('visibilitychange', syncAnimation);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      observer.disconnect();
      reducedMotion.removeEventListener('change', syncAnimation);
      document.removeEventListener('visibilitychange', syncAnimation);
    };
  }, []);

  return <svg ref={scene} className={styles.scene} viewBox="0 0 1000 220" data-visible="false" aria-hidden="true" focusable="false">
    <g ref={school} transform="translate(500 110)">
      {fish.map(({ x, y, size }, index) => <g key={index} transform={`translate(${x} ${y}) scale(${size})`}>
          <g className={styles.fish} style={{ '--phase': `${-index * .19}s`, '--tail-speed': `${.65 + (index % 4) * .08}s` } as CSSProperties}>
            <g className={styles.tail}>
              <path d="M-9 0-18-4-18 4Z"/>
              <circle cx="-18" cy="-4" r=".65"/>
              <circle cx="-18" cy="4" r=".65"/>
            </g>
            <path d="M-9 0 5-8 5 8Z M5-8 19 0 5 8Z"/>
            <g className={styles.joints}>
              <circle cx="-9" r=".7"/>
              <circle cx="5" cy="-8" r=".7"/>
              <circle cx="5" cy="8" r=".7"/>
              <circle cx="19" r=".7"/>
              <circle cx="11" cy="-2.3" r=".85"/>
            </g>
          </g>
      </g>)}
    </g>
  </svg>;
}
