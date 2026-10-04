/** The root layout keeps the graph alive when this route unmounts. */
export default function BodyGraph({ compact = false }: { compact?: boolean }) {
  return <div className={`body-graph ${compact ? 'compact' : ''}`} data-connection-anchor="body" aria-hidden="true"/>;
}
