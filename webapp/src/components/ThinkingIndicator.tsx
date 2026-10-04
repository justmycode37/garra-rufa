import { useId } from 'react';

export default function ThinkingIndicator({ label = 'Thinking…' }: { label?: string }) {
  return <div className="ai-thinking" role="status">
    <span>{label}</span>
    <FishAnimation/>
  </div>;
}

export function FishAnimation({ compact = false }: { compact?: boolean }) {
  const surfaceId = useId();

  return <svg className={`ai-fish-scene${compact ? ' ai-fish-compact' : ''}`} viewBox="0 0 72 58" aria-hidden="true" focusable="false">
    <defs>
      <clipPath id={surfaceId}><rect width="72" height="43"/></clipPath>
    </defs>

    <g clipPath={`url(#${surfaceId})`}>
      <g className="ai-fish-anchor">
        <g className="ai-fish-leap">
          {/* The same three connected triangles and eye as the brand mark. */}
          <g className="ai-fish-mark">
            <g className="ai-fish-tail">
              <path d="M-9 0-18-4-18 4Z"/>
              <circle cx="-18" cy="-4" r=".65"/>
              <circle cx="-18" cy="4" r=".65"/>
            </g>
            <path className="ai-fish-body" d="M-9 0 5-8 5 8Z"/>
            <path d="M5-8 19 0 5 8Z"/>
            <g className="ai-fish-joints">
              <circle cx="-9" r=".7"/>
              <circle cx="5" cy="-8" r=".7"/>
              <circle cx="5" cy="8" r=".7"/>
              <circle cx="19" r=".7"/>
              <circle cx="11" cy="-2.3" r=".85"/>
            </g>
          </g>
        </g>
      </g>
    </g>

    <g className="ai-fish-takeoff" transform="translate(36 43)">
      <g className="ai-fish-splash">
        <path className="ai-fish-drop-left" d="M-2 0l-1-2"/>
        <path className="ai-fish-drop-middle" d="M0 0v-2.5"/>
        <path className="ai-fish-drop-right" d="M2 0l1-2"/>
      </g>
    </g>
    <g className="ai-fish-landing" transform="translate(36 43)">
      <g className="ai-fish-splash">
        <path className="ai-fish-drop-left" d="M-2 0l-1-2"/>
        <path className="ai-fish-drop-middle" d="M0 0v-2.5"/>
        <path className="ai-fish-drop-right" d="M2 0l1-2"/>
      </g>
    </g>
  </svg>;
}
