'use client';

import { useCallback, useEffect, useRef, type RefObject } from 'react';

export function useChatScroll(container: RefObject<HTMLDivElement | null>, enabled = true) {
  const following = useRef(true);
  const frame = useRef<number | undefined>(undefined);
  const lastPosition = useRef<number | undefined>(undefined);
  const reducedMotion = useRef(false);
  const stop = useCallback(() => { if (frame.current !== undefined) cancelAnimationFrame(frame.current); frame.current = undefined; }, []);
  const follow = useCallback(() => {
    if (!enabled || !following.current || frame.current !== undefined) return;
    let lastTime = performance.now();
    const tick = (time: number) => {
      frame.current = undefined;
      const element = container.current;
      if (!element || !following.current) return;
      const target = Math.max(0, element.scrollHeight - element.clientHeight);
      const gap = target - element.scrollTop;
      const step = reducedMotion.current ? 1 : 1 - Math.exp(-Math.min(64, time - lastTime) / 110);
      lastTime = time;
      element.scrollTop = Math.abs(gap) < .75 ? target : element.scrollTop + gap * step;
      lastPosition.current = element.scrollTop;
      if (Math.abs(target - element.scrollTop) > .75) frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
  }, [container, enabled]);
  useEffect(() => {
    const element = container.current;
    if (!element || !enabled) return;
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const updateMotion = () => { reducedMotion.current = preference.matches; };
    updateMotion(); preference.addEventListener('change', updateMotion);
    const resize = new ResizeObserver(follow);
    resize.observe(element);
    for (const child of element.children) resize.observe(child);
    const changes = new MutationObserver(() => { for (const child of element.children) resize.observe(child); follow(); });
    changes.observe(element, { childList: true, subtree: true, characterData: true });
    const pause = () => { following.current = false; lastPosition.current = undefined; stop(); };
    const wheel = (event: WheelEvent) => { if (event.deltaY < 0) pause(); };
    const key = (event: KeyboardEvent) => { if (['ArrowUp', 'PageUp', 'Home'].includes(event.key)) pause(); };
    const scroll = () => {
      if (lastPosition.current !== undefined && Math.abs(element.scrollTop - lastPosition.current) < 1) return;
      following.current = element.scrollHeight - element.scrollTop - element.clientHeight < (following.current ? 60 : 2);
      if (following.current) follow(); else stop();
    };
    element.addEventListener('wheel', wheel, { passive: true });
    element.addEventListener('touchmove', pause, { passive: true });
    element.addEventListener('keydown', key);
    element.addEventListener('scroll', scroll, { passive: true });
    follow();
    return () => {
      stop(); resize.disconnect(); changes.disconnect(); preference.removeEventListener('change', updateMotion);
      element.removeEventListener('wheel', wheel); element.removeEventListener('touchmove', pause); element.removeEventListener('keydown', key); element.removeEventListener('scroll', scroll);
    };
  }, [container, enabled, follow, stop]);
  return useCallback(() => { following.current = true; lastPosition.current = undefined; follow(); }, [follow]);
}
