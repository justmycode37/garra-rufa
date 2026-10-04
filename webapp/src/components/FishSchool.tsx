'use client';

import { useEffect, useRef, useState, type CSSProperties } from 'react';
import styles from './FishSchool.module.css';

const fish = [
  { x: 140, y: 139, size: .85, dx: -34, dy: 19, tilt: -15 },
  { x: 213, y: 90, size: 1, dx: -15, dy: -23, tilt: 12 },
  { x: 220, y: 190, size: .8, dx: -21, dy: 22, tilt: -8 },
  { x: 286, y: 139, size: 1.15, dx: -10, dy: -18, tilt: 9 },
  { x: 326, y: 63, size: .82, dx: -18, dy: -14, tilt: -12 },
  { x: 328, y: 214, size: .94, dx: 12, dy: 17, tilt: 11 },
  { x: 380, y: 103, size: 1.1, dx: -16, dy: -17, tilt: -8 },
  { x: 401, y: 171, size: 1.25, dx: -7, dy: 13, tilt: 7 },
  { x: 456, y: 54, size: .8, dx: 15, dy: -12, tilt: 14 },
  { x: 463, y: 225, size: .84, dx: -15, dy: 10, tilt: -9 },
  { x: 487, y: 132, size: 1.38, dx: 20, dy: -8, tilt: -5 },
  { x: 559, y: 78, size: .97, dx: 24, dy: -21, tilt: -13 },
  { x: 566, y: 189, size: 1.04, dx: 23, dy: 24, tilt: 12 },
  { x: 646, y: 130, size: 1.19, dx: 28, dy: -13, tilt: 7 },
  { x: 672, y: 209, size: .76, dx: 36, dy: 15, tilt: -10 },
  { x: 738, y: 102, size: .84, dx: 25, dy: -18, tilt: -12 },
];

export default function FishSchool() {
  const scene = useRef<SVGSVGElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!scene.current) return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold: .1 });
    observer.observe(scene.current);
    return () => observer.disconnect();
  }, []);

  return <svg ref={scene} className={styles.scene} viewBox="0 0 880 280" preserveAspectRatio="xMidYMid slice" data-visible={visible} aria-hidden="true" focusable="false">
    <g className={styles.current}>
      {fish.map(({ x, y, size, dx, dy, tilt }, index) => <g key={index} transform={`translate(${x} ${y}) scale(${size})`} opacity={size < .9 ? .45 : size < 1.1 ? .7 : 1}>
        <g className={styles.gather} style={{ '--wander-x': `${dx}px`, '--wander-y': `${dy}px`, '--wander-angle': `${tilt}deg` } as CSSProperties}>
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
        </g>
      </g>)}
    </g>
  </svg>;
}
