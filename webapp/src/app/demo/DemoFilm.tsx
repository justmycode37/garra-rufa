'use client';

import { useEffect, useRef, useState } from 'react';
import { Maximize2, Pause, Play, RotateCcw, SlidersHorizontal, X } from 'lucide-react';
import { FishAnimation } from '@/components/ThinkingIndicator';
import { FILM_CUES, FILM_DURATION, FISH_EDGES, FISH_NODES, NARRATION_COPY, OPENING_COPY, brandClosingPose, brandFishPosition, brandSplashPosition, filmContentTime, finalePose, fishPose, nodePose, out, progress, textPose, type BrandLayout } from '@/lib/demo-film';
import { createDemoHands, type DemoHands } from '@/lib/demo-hands';
import styles from './demo.module.css';

type Playback = { seek: (time: number) => void; toggle: () => void; replay: () => void };

function Fish({ leader = false, standalone = false }: { leader?: boolean; standalone?: boolean }) {
  return <g data-fish={standalone ? undefined : ''} opacity={leader || standalone ? 1 : 0}>
    <g data-tail={standalone ? undefined : ''}>
      {leader ? FISH_EDGES.slice(5).map((edge, i) => <line key={i} data-edge={i + 5} pathLength="1"/>) : <path d="M-135 0-270-60-270 60Z"/>}
      {FISH_NODES.slice(4).map((point, i) => <circle key={i} data-node={leader ? i + 4 : undefined} cx={point.x} cy={point.y} r="4.8"/>)}
    </g>
    {leader ? FISH_EDGES.slice(0, 5).map((edge, i) => <line key={i} data-edge={i} pathLength="1"/>) : <path d="M-135 0 75-120 75 120Z M75-120 285 0 75 120"/>}
    {FISH_NODES.slice(0, 4).map((point, i) => <circle key={i} data-node={leader ? i : undefined} cx={point.x} cy={point.y} r="4.8"/>)}
    <circle data-eye={leader ? '' : undefined} cx="165" cy="-34.5" r="8"/>
  </g>;
}

function Words({ text, start, end, stagger = .065 }: { text: string; start: number; end: number; stagger?: number }) {
  return text.split(' ').map((word, index) => <span className={styles.wordMask} key={index}><span data-word="" data-start={start + index * stagger} data-end={end + index * .027}>{word}</span>{' '}</span>);
}

export default function DemoFilm() {
  const root = useRef<HTMLElement>(null);
  const playback = useRef<Playback | null>(null);
  const [playing, setPlaying] = useState(false);
  const [clean, setClean] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const words = Array.from(element.querySelectorAll<HTMLElement>('[data-word]')).map(node => ({ node, start: Number(node.dataset.start), end: Number(node.dataset.end) }));
    const nodes = Array.from(element.querySelectorAll<SVGCircleElement>('[data-node]')).sort((a, b) => Number(a.dataset.node) - Number(b.dataset.node));
    const edges = Array.from(element.querySelectorAll<SVGLineElement>('[data-edge]')).sort((a, b) => Number(a.dataset.edge) - Number(b.dataset.edge));
    const fishes = Array.from(element.querySelectorAll<SVGGElement>('[data-fish]'));
    const tails = Array.from(element.querySelectorAll<SVGGElement>('[data-tail]'));
    const eye = element.querySelector<SVGCircleElement>('[data-eye]')!;
    const white = element.querySelector<HTMLElement>('[data-white]')!;
    const jump = element.querySelector<HTMLElement>('[data-jump]')!;
    const jumpAnimations = jump.getAnimations({ subtree: true });
    jumpAnimations.forEach(animation => { animation.pause(); animation.currentTime = 0; });
    const handCanvas = element.querySelector<HTMLCanvasElement>('[data-hands]')!;
    const handFallback = element.querySelector<HTMLElement>('[data-hand-fallbacks]')!;
    const fallbackArms = Array.from(handFallback.querySelectorAll<HTMLElement>('[data-arm]'));
    const pair = element.querySelector<SVGGElement>('[data-closing-pair]')!;
    const pairDots = Array.from(pair.querySelectorAll<SVGCircleElement>('circle'));
    const pairLine = pair.querySelector<SVGLineElement>('line')!;
    const outroJump = element.querySelector<HTMLElement>('[data-outro-jump]')!;
    const outroAnimations = outroJump.getAnimations({ subtree: true });
    outroAnimations.forEach(animation => { animation.pause(); animation.currentTime = 0; });
    const brandFish = element.querySelector<SVGSVGElement>('[data-brand-fish]')!;
    const fishSlot = element.querySelector<HTMLElement>('[data-fish-slot]')!;
    const wordmark = element.querySelector<HTMLElement>('[data-wordmark]')!;
    const period = element.querySelector<HTMLElement>('[data-brand-period]')!;
    const splashDot = element.querySelector<HTMLElement>('[data-brand-splash]')!;
    const impactRipple = element.querySelector<HTMLElement>('[data-brand-impact]')!;
    const droplets = Array.from(element.querySelectorAll<HTMLElement>('[data-brand-droplet]'));
    let brandLayout: BrandLayout | undefined;
    const measureBrand = () => {
      const bounds = element.getBoundingClientRect(), slot = fishSlot.getBoundingClientRect(), dot = period.getBoundingClientRect(), letters = wordmark.getBoundingClientRect();
      brandLayout = { fishWidth: slot.width, fishX: slot.left - bounds.left + slot.width / 2, fishY: slot.top - bounds.top + slot.height / 2, wordmarkLeft: letters.left - bounds.left, wordmarkTop: letters.top - bounds.top, periodX: dot.left - bounds.left + dot.width / 2, periodY: dot.top - bounds.top + dot.height / 2, periodSize: dot.width };
    };
    const timeline = element.querySelector<HTMLInputElement>('[data-timeline]')!;
    const clock = element.querySelector<HTMLOutputElement>('[data-clock]')!;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const params = new URLSearchParams(window.location.search);
    const requestedTime = params.get('t');
    let time = requestedTime !== null && Number.isFinite(Number(requestedTime)) ? Math.max(0, Math.min(FILM_DURATION, Number(requestedTime))) : 0;
    let running = !motion.matches && requestedTime === null;
    let previous: number | null = null;
    let frame = 0;
    let disposed = false;
    let hands: DemoHands | undefined;
    setClean(params.get('clean') === '1');

    const draw = () => {
      const contentTime = filmContentTime(time);
      const reduced = motion.matches;
      for (const { node, start, end } of words) {
        const pose = textPose(contentTime, start, end);
        node.style.opacity = String(pose.opacity);
        node.style.transform = reduced ? 'none' : `translate3d(${pose.x}px, 0, 0)`;
      }
      const positions = FISH_NODES.map((_, index) => nodePose(contentTime, index));
      nodes.forEach((node, index) => {
        node.setAttribute('cx', String(positions[index].x));
        node.setAttribute('cy', String(positions[index].y));
        node.setAttribute('r', String(positions[index].radius));
        node.style.opacity = String(positions[index].opacity);
      });
      edges.forEach((line, index) => {
        const edge = FISH_EDGES[index];
        const from = positions[edge.from], to = positions[edge.to];
        line.setAttribute('x1', String(from.x));
        line.setAttribute('y1', String(from.y));
        line.setAttribute('x2', String(to.x));
        line.setAttribute('y2', String(to.y));
        line.style.strokeDasharray = '1';
        line.style.strokeDashoffset = String(1 - out(progress(contentTime, edge.at, edge.at + .23)));
        line.style.opacity = contentTime < edge.at ? '0' : '1';
      });
      eye.style.opacity = String(progress(contentTime, FILM_CUES.formed, FILM_CUES.formed + .12));
      fishes.forEach((fish, index) => {
        const pose = fishPose(contentTime, index);
        fish.setAttribute('transform', `translate(${pose.x} ${pose.y}) rotate(${reduced ? 0 : pose.rotation}) scale(${pose.scale})`);
        fish.style.opacity = String(pose.opacity);
        tails[index].setAttribute('transform', `rotate(${reduced ? 0 : pose.tail} -135 0)`);
      });
      const ending = finalePose(contentTime);
      jump.style.opacity = String(ending.jumpOpacity);
      jumpAnimations.forEach(animation => { animation.currentTime = ending.jumpAnimationTime; });
      white.style.clipPath = `circle(${ending.white * 150}% at 50% 60%)`;
      hands?.render(contentTime, reduced);
      fallbackArms.forEach((arm, index) => {
        arm.style.transform = `translateX(${(1 - ending.hands) * (index === 0 ? -110 : 110)}%)`;
        arm.style.opacity = contentTime < FILM_CUES.hands.start ? '0' : String(ending.handsOpacity);
      });
      const brand = brandClosingPose(contentTime);
      pair.style.opacity = String(brand.pairOpacity);
      pairDots[0].setAttribute('r', String(brand.firstDot * 7));
      pairDots[1].setAttribute('r', String(brand.secondDot * 7));
      pairLine.style.strokeDashoffset = String(1 - brand.connection);
      outroJump.style.opacity = String(brand.jumpOpacity);
      outroAnimations.forEach(animation => { animation.currentTime = brand.jumpAnimationTime; });
      if (!brandLayout) measureBrand();
      const layout = brandLayout!;
      const fish = brandFishPosition(contentTime, layout);
      outroJump.style.left = `${fish.x}px`;
      outroJump.style.top = `${layout.fishY}px`;
      outroJump.style.width = `${layout.fishWidth * 2.08}px`;
      brandFish.style.width = `${layout.fishWidth}px`;
      brandFish.style.height = `${layout.fishWidth / 2}px`;
      brandFish.style.opacity = String(brand.fishOpacity);
      brandFish.style.transform = `translate3d(${fish.x - layout.fishWidth / 2}px, ${fish.y - layout.fishWidth / 4}px, 0) rotate(${reduced ? 0 : fish.rotation}deg)`;
      wordmark.style.opacity = String(brand.wordmarkOpacity);
      period.style.opacity = String(brand.periodOpacity);
      const dot = brandSplashPosition(contentTime, layout);
      splashDot.style.width = splashDot.style.height = `${layout.periodSize}px`;
      splashDot.style.opacity = String(brand.splashOpacity);
      splashDot.style.transform = `translate3d(${dot.x - layout.periodSize / 2}px, ${dot.y - layout.periodSize / 2}px, 0)`;
      const rippleWidth = layout.fishWidth * (.12 + brand.impactSpread * .48);
      const rippleHeight = rippleWidth * .16;
      impactRipple.style.width = `${rippleWidth}px`;
      impactRipple.style.height = `${rippleHeight}px`;
      impactRipple.style.opacity = String(brand.impactOpacity * .65);
      impactRipple.style.transform = `translate3d(${fish.contactX - rippleWidth / 2}px, ${fish.surfaceY - rippleHeight / 2}px, 0)`;
      droplets.forEach((drop, index) => {
        const drift = (index === 0 ? -1 : 1) * brand.dropletsTravel * layout.fishWidth * .23;
        const rise = 4 * brand.dropletsTravel * (1 - brand.dropletsTravel) * layout.fishWidth * (index === 0 ? .16 : .24);
        drop.style.opacity = String(brand.dropletsOpacity);
        drop.style.transform = `translate3d(${fish.contactX + drift}px, ${fish.surfaceY - rise}px, 0)`;
      });
      timeline.value = String(time);
      timeline.style.setProperty('--progress', `${time / FILM_DURATION * 100}%`);
      clock.value = `${time.toFixed(1).padStart(4, '0')} / ${FILM_DURATION.toFixed(1)}`;
      element.dataset.time = time.toFixed(2);
      element.dataset.contentTime = contentTime.toFixed(2);
    };

    const tick = (now: number) => {
      if (!running || disposed) return;
      if (previous !== null) time = Math.min(FILM_DURATION, time + (now - previous) / 1000);
      previous = now;
      draw();
      if (time >= FILM_DURATION) { running = false; setPlaying(false); return; }
      frame = requestAnimationFrame(tick);
    };
    const pause = () => { running = false; previous = null; cancelAnimationFrame(frame); setPlaying(false); };
    const play = () => {
      cancelAnimationFrame(frame);
      if (time >= FILM_DURATION) time = 0;
      previous = null;
      running = true;
      setPlaying(true);
      frame = requestAnimationFrame(tick);
    };
    playback.current = {
      seek: value => { pause(); time = Math.max(0, Math.min(FILM_DURATION, value)); draw(); },
      toggle: () => { if (running) pause(); else play(); },
      replay: () => { time = 0; draw(); play(); },
    };
    const onKey = (event: KeyboardEvent) => {
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (target && (target.isContentEditable || /INPUT|TEXTAREA|SELECT/.test(target.tagName))) return;
      if (event.code === 'Space' && !target?.closest('button, a')) { event.preventDefault(); playback.current?.toggle(); }
      if (event.key.toLowerCase() === 'r') playback.current?.replay();
      if (event.key.toLowerCase() === 'h') setClean(value => !value);
      if (event.key === 'Escape') setClean(false);
    };
    const onVisibility = () => { if (document.hidden) pause(); };
    const onMotion = () => { if (motion.matches) pause(); draw(); };
    const onResize = () => { brandLayout = undefined; draw(); };
    document.addEventListener('keydown', onKey);
    document.addEventListener('visibilitychange', onVisibility);
    motion.addEventListener('change', onMotion);
    window.addEventListener('resize', onResize);
    const onContextLost = (event: Event) => {
      event.preventDefault();
      hands?.dispose(); hands = undefined;
      handCanvas.style.visibility = 'hidden';
      handFallback.hidden = false;
      draw();
    };
    handCanvas.addEventListener('webglcontextlost', onContextLost);
    const handsReady = createDemoHands(handCanvas).then(scene => {
      if (disposed) { scene.dispose(); return; }
      hands = scene;
    }).catch(() => { if (!disposed) handFallback.hidden = false; });
    // Preload the exact About arms before playback so the final shot cannot arrive late.
    void Promise.all([document.fonts.ready, handsReady]).then(() => {
      if (disposed) return;
      if (motion.matches && requestedTime === null) time = FILM_DURATION;
      measureBrand();
      draw();
      if (running) play();
    });
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('visibilitychange', onVisibility);
      motion.removeEventListener('change', onMotion);
      window.removeEventListener('resize', onResize);
      handCanvas.removeEventListener('webglcontextlost', onContextLost);
      hands?.dispose();
      playback.current = null;
    };
  }, []);

  const fullscreen = async () => {
    try {
      if (!document.fullscreenElement) await root.current?.requestFullscreen();
      else await document.exitFullscreen();
    } catch { setNotice('Fullscreen is unavailable here. Open this page in your browser for a full-screen recording.'); }
  };

  return <main ref={root} className={styles.film} data-clean={clean} aria-label="Garra rufa brand film">
    <h1 className={styles.srOnly}>Connect the dots — a garra rufa film</h1>
    <p className={styles.srOnly}>{OPENING_COPY}. Dots form a fish. “hard problems, require teamwork, to be solved” appears as five fish swim away. A fish jumps, wiping the screen white. “So, what do we do?” Then “We connect” as the About page’s two graph arms reach in from opposite sides. Narration cue: {NARRATION_COPY} Two dots connect for 1.5 seconds, followed by ten seconds of blank white screen. A fish jumps twice, landing beside garra rufa as a splash becomes the brand’s period.</p>
    <div className={styles.artwork} aria-hidden="true">
      <div className={styles.opening}>
        <div><Words text="To understand," start={.08} end={FILM_CUES.openingOut} stagger={.07}/></div>
        <div><Words text="means to connect" start={.3} end={FILM_CUES.openingOut + .02} stagger={.05}/></div>
        <div><Words text="the dots" start={.52} end={FILM_CUES.openingOut + .04} stagger={.06}/></div>
      </div>
      <svg className={styles.scene} viewBox="0 0 1600 900" fill="none" stroke="currentColor" strokeWidth="3.4" strokeLinejoin="round" strokeLinecap="round">
        <Fish leader/>
        {Array.from({ length: 4 }, (_, index) => <Fish key={index}/>)}
      </svg>
      <div className={`${styles.caption} ${styles.behindFish}`}><Words text="hard problems," {...FILM_CUES.hardProblems}/></div>
      <div className={styles.caption}><Words text="require teamwork," {...FILM_CUES.teamwork}/></div>
      <div className={styles.caption}><Words text="to be solved" {...FILM_CUES.solved}/></div>
      <div className={styles.whiteWipe} data-white=""/>
      <div className={styles.jumpFish} data-jump=""><FishAnimation/></div>
      <div className={styles.finalQuestion}><Words text="So, what do we do?" {...FILM_CUES.question} stagger={.045}/></div>
      <canvas className={styles.hands} data-hands=""/>
      <div className={styles.handFallbacks} data-hand-fallbacks="" hidden>
        <div data-arm="left"><img src="/models/hands-fallback.svg" alt=""/></div>
        <div data-arm="right"><img src="/models/hands-fallback.svg" alt=""/></div>
      </div>
      <div className={styles.connectCaption}><Words text="We connect" {...FILM_CUES.connect}/></div>
      <svg className={styles.closingDots} viewBox="0 0 1600 900">
        <g data-closing-pair="" opacity="0">
          <line x1="625" y1="450" x2="975" y2="450" pathLength="1" stroke="currentColor" strokeWidth="3" strokeDasharray="1" strokeDashoffset="1"/>
          <circle cx="625" cy="450" r="0"/><circle cx="975" cy="450" r="0"/>
        </g>
      </svg>
      <div className={`${styles.jumpFish} ${styles.outroJump}`} data-outro-jump=""><FishAnimation/></div>
      <div className={styles.brandFinale}>
        <div className={styles.brandLockup}>
          <span className={styles.fishSlot} data-fish-slot=""/>
          <span className={styles.wordmark} data-wordmark="">garra rufa<span className={styles.brandDot} data-brand-period=""/></span>
        </div>
        <svg className={styles.brandFish} data-brand-fish="" viewBox="-310 -155 620 310" fill="none" stroke="currentColor" strokeWidth="6" strokeLinejoin="round" strokeLinecap="round"><Fish standalone/></svg>
        <span className={styles.brandSplash} data-brand-splash=""/>
        <span className={styles.brandImpact} data-brand-impact=""/>
        <span className={styles.brandDroplet} data-brand-droplet=""/><span className={styles.brandDroplet} data-brand-droplet=""/>
      </div>
    </div>
    <div className={styles.chrome}>
      <a href="/" className={styles.brand}>garra rufa<span>.</span></a>
      <span className={styles.filmLabel}>CONNECT THE DOTS <span> / </span> BRAND FILM</span>
      <button className={styles.cleanButton} onClick={() => setClean(true)} title="Hide controls (H)"><X size={14}/><span>Clean view</span></button>
    </div>
    <div className={styles.controls} role="group" aria-label="Film playback">
      <button onClick={() => playback.current?.toggle()} aria-label={playing ? 'Pause film' : 'Play film'} title="Play / pause (Space)">{playing ? <Pause size={16}/> : <Play size={16}/>}</button>
      <button onClick={() => playback.current?.replay()} aria-label="Replay film" title="Replay (R)"><RotateCcw size={16}/></button>
      <input data-timeline="" type="range" min="0" max={FILM_DURATION} step=".05" defaultValue="0" aria-label="Film position in seconds" onChange={event => playback.current?.seek(Number(event.target.value))}/>
      <output data-clock="" className={styles.clock}>00.0 / {FILM_DURATION.toFixed(1)}</output>
      <button onClick={fullscreen} aria-label="Toggle fullscreen" title="Fullscreen"><Maximize2 size={16}/></button>
    </div>
    <p className={styles.hint}>Space to pause <span>·</span> R to replay <span>·</span> H to hide controls</p>
    <button className={styles.restore} onClick={() => setClean(false)} aria-label="Show film controls" title="Show controls (H)"><SlidersHorizontal size={16}/></button>
    {notice && <p className={styles.notice} role="status">{notice}</p>}
  </main>;
}
