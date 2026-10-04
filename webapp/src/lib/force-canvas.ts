// Canvas force-graph renderer shared by the graph pages: d3-force physics (many-body,
// links, collision, per-node targets), d3-zoom pan / zoom, node dragging, hover and
// selection neighbourhoods, gradient and blended colours, and de-cluttered labels.
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type Simulation, type SimulationNodeDatum } from 'd3-force';
import { select, type Selection } from 'd3-selection';
import { zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from 'd3-zoom';

export type GraphNode = SimulationNodeDatum & {
  id: string;
  label: string;
  /** circle radius; pills size themselves to their text */
  r: number;
  shape?: 'circle' | 'pill' | 'square';
  /** one colour, or several blended around the node (conic) */
  fill: string | string[];
  stroke?: string;
  strokeWidth?: number;
  /** text inside a pill (label is drawn inside, this as a second, quieter part) */
  badge?: string;
  textColor?: string;
  /** labels with a higher priority are shown first when space is short */
  priority?: number;
  /** always label, whatever the zoom */
  pinLabel?: boolean;
  /** where the node is pulled to (forceX / forceY), with this strength */
  tx?: number; ty?: number; pull?: number;
  charge?: number;
  /** id of a node new nodes appear next to */
  spawn?: string;
  fixed?: boolean;
  /** region (see setGraph) whose territory this node belongs to */
  group?: string;
  w?: number; h?: number;
};

/** A soft, labelled territory drawn under the nodes of one group. */
export type GraphRegion = { label: string; color: string };

export type GraphLink = {
  id: string;
  source: string;
  target: string;
  color?: string;
  /** gradient from the source's to the target's colour */
  colors?: [string, string];
  width: number;
  opacity?: number;
  dash?: number[];
  arrow?: boolean;
  curve?: number;
  label?: string;
  /** spring length / strength; physics: false draws the link without pulling */
  distance?: number;
  strength?: number;
  physics?: boolean;
};

export type ForceCanvasOptions = {
  onClick?: (hit: { node?: GraphNode; link?: GraphLink } | null) => void;
  onHover?: (hit: { node?: GraphNode; link?: GraphLink } | null) => void;
  /** also hit-test links (for clickable edges) */
  linkHits?: boolean;
  /** zoom level from which every label is drawn */
  labelZoom?: number;
  showLinkLabels?: () => boolean;
  halo?: string;
  font?: string;
  chargeStrength?: number;
  collidePadding?: number;
  /** screen space covered by overlays at the sides, left out when fitting */
  insets?: { left: number; right: number };
};

type LinkDatum = { source: string | GraphNode; target: string | GraphNode; distance: number; strength: number };

const reduceMotion = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
const PILL_FONT = 13, PILL_PAD_X = 16, PILL_H = 34;

export class ForceCanvas {
  readonly canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private opts: ForceCanvasOptions;
  private sim: Simulation<GraphNode, LinkDatum>;
  private zoomer: ZoomBehavior<HTMLCanvasElement, unknown>;
  private sel: Selection<HTMLCanvasElement, unknown, null, undefined>;
  private transform: ZoomTransform = zoomIdentity;
  private nodes: GraphNode[] = [];
  private links: GraphLink[] = [];
  private byId = new Map<string, GraphNode>();
  private neighbours = new Map<string, Set<string>>();
  private hover: { node?: GraphNode; link?: GraphLink } | null = null;
  private selected: string | null = null;
  private dragging: { node: GraphNode; startX: number; startY: number; moved: boolean; wasFixed: boolean } | null = null;
  private down: { x: number; y: number } | null = null;
  private frame = 0;
  private width = 1;
  private height = 1;
  private resize: ResizeObserver;
  private animation = 0;
  private fitted = false;
  private regions: Record<string, GraphRegion> = {};

  constructor(canvas: HTMLCanvasElement, opts: ForceCanvasOptions = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d')!;
    this.opts = opts;
    this.sim = forceSimulation<GraphNode, LinkDatum>()
      .force('charge', forceManyBody<GraphNode>().strength(n => n.charge ?? opts.chargeStrength ?? -160).theta(0.9).distanceMax(900))
      .force('link', forceLink<GraphNode, LinkDatum>().id(n => n.id).distance(l => l.distance).strength(l => l.strength))
      .force('collide', forceCollide<GraphNode>(n => this.radius(n) + (opts.collidePadding ?? 6)).strength(0.9).iterations(2))
      .force('x', forceX<GraphNode>(n => n.tx ?? 0).strength(n => n.pull ?? 0.02))
      .force('y', forceY<GraphNode>(n => n.ty ?? 0).strength(n => n.pull ?? 0.02))
      .alphaDecay(0.025).velocityDecay(0.35)
      .on('tick', () => this.request());
    if (reduceMotion()) this.sim.stop();

    this.sel = select(canvas);
    this.zoomer = zoom<HTMLCanvasElement, unknown>().scaleExtent([0.08, 8])
      .filter(event => {
        if (event.type === 'wheel') return true;
        if (event.button) return false;
        const p = 'touches' in event && event.touches.length ? event.touches[0] : event;
        return !this.nodeAt(p.clientX, p.clientY);
      })
      .on('zoom', event => { this.transform = event.transform; this.request(); });
    this.sel.call(this.zoomer).on('dblclick.zoom', null);

    canvas.addEventListener('pointerdown', this.onDown);
    canvas.addEventListener('pointermove', this.onMove);
    canvas.addEventListener('pointerup', this.onUp);
    canvas.addEventListener('pointercancel', this.onUp);
    canvas.addEventListener('pointerleave', this.onLeave);
    this.resize = new ResizeObserver(() => this.measure());
    this.resize.observe(canvas);
    this.measure();
    this.sel.call(this.zoomer.transform, zoomIdentity.translate(this.width / 2, this.height / 2).scale(0.6));
  }

  destroy() {
    this.sim.stop();
    this.resize.disconnect();
    cancelAnimationFrame(this.frame);
    cancelAnimationFrame(this.animation);
    this.sel.on('.zoom', null);
    this.canvas.removeEventListener('pointerdown', this.onDown);
    this.canvas.removeEventListener('pointermove', this.onMove);
    this.canvas.removeEventListener('pointerup', this.onUp);
    this.canvas.removeEventListener('pointercancel', this.onUp);
    this.canvas.removeEventListener('pointerleave', this.onLeave);
  }

  /** Replace the graph; nodes keep their positions by id, new ones appear by their spawn. */
  setGraph(nodes: GraphNode[], links: GraphLink[], { energy = 0.6, regions = {} }: { energy?: number; regions?: Record<string, GraphRegion> } = {}) {
    this.regions = regions;
    const old = this.byId;
    this.byId = new Map();
    for (const n of nodes) {
      const prev = old.get(n.id);
      if (prev) Object.assign(n, { x: prev.x, y: prev.y, vx: prev.vx, vy: prev.vy, fx: n.fixed ? n.fx : prev.fixed ? undefined : prev.fx, fy: n.fixed ? n.fy : prev.fixed ? undefined : prev.fy });
      else if (n.x == null) {
        const at = (n.spawn && (this.byId.get(n.spawn) ?? old.get(n.spawn))) || null;
        const jitter = () => (Math.random() - 0.5) * 40;
        n.x = (at?.x ?? n.tx ?? 0) + jitter();
        n.y = (at?.y ?? n.ty ?? 0) + jitter();
      }
      if (n.fixed) { n.fx = n.fx ?? n.x; n.fy = n.fy ?? n.y; }
      if (n.shape === 'pill') this.sizePill(n);
      this.byId.set(n.id, n);
    }
    this.nodes = nodes;
    this.links = links.filter(l => this.byId.has(l.source) && this.byId.has(l.target) && l.source !== l.target);
    this.neighbours = new Map();
    for (const l of this.links) {
      if (!this.neighbours.has(l.source)) this.neighbours.set(l.source, new Set());
      if (!this.neighbours.has(l.target)) this.neighbours.set(l.target, new Set());
      this.neighbours.get(l.source)!.add(l.target);
      this.neighbours.get(l.target)!.add(l.source);
    }
    if (this.hover?.node && !this.byId.has(this.hover.node.id)) this.hover = null;
    if (this.hover?.link && !this.links.includes(this.hover.link)) this.hover = null;
    this.sim.nodes(this.nodes);
    const physical = this.links.filter(l => l.physics !== false)
      .map(l => ({ source: l.source, target: l.target, distance: l.distance ?? 60, strength: l.strength ?? 0.4 }));
    this.sim.force<ReturnType<typeof forceLink<GraphNode, LinkDatum>>>('link')!.links(physical);
    (this.sim.force('collide') as ReturnType<typeof forceCollide<GraphNode>>).initialize(this.nodes, Math.random);
    if (reduceMotion()) { this.sim.alpha(1); this.sim.tick(old.size ? 120 : 320); this.request(); }
    else this.sim.alpha(Math.max(this.sim.alpha(), energy)).restart();
    this.request();
  }

  setSelected(id: string | null) { this.selected = id; this.request(); }

  /** Kick the simulation, e.g. after changing forces. */
  reheat(energy = 0.3) {
    if (reduceMotion()) { this.sim.tick(80); this.request(); return; }
    this.sim.alpha(energy).restart();
  }

  fit({ padding = 48, duration = 600 }: { padding?: number; duration?: number } = {}) {
    if (!this.nodes.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of this.nodes) {
      const r = this.radius(n);
      x0 = Math.min(x0, n.x! - r); y0 = Math.min(y0, n.y! - r); x1 = Math.max(x1, n.x! + r); y1 = Math.max(y1, n.y! + r);
    }
    // floating panels cover the sides: fit into the area between them
    const inset = this.width > 860 ? this.opts.insets ?? { left: 0, right: 0 } : { left: 0, right: 0 };
    const width = Math.max(200, this.width - inset.left - inset.right);
    const k = Math.min(2, Math.max(0.08, Math.min((width - 2 * padding) / (x1 - x0 || 1), (this.height - 2 * padding) / (y1 - y0 || 1))));
    const cx = (this.width - width > 0 ? inset.left : 0) + width / 2;
    this.animateTo(zoomIdentity.translate(cx - k * (x0 + x1) / 2, this.height / 2 - k * (y0 + y1) / 2).scale(k), duration);
  }

  /** Fit once, when the first layout has mostly settled. */
  fitWhenSettled() {
    this.fitted = false;
    const check = () => {
      if (this.fitted) return;
      if (this.nodes.length && (this.sim.alpha() < 0.2 || reduceMotion())) { this.fitted = true; this.fit({ duration: 500 }); }
      else setTimeout(check, 120);
    };
    check();
  }

  focus(id: string, scale?: number) {
    const n = this.byId.get(id);
    if (!n) return;
    const k = scale ?? Math.max(this.transform.k, 1.2);
    const inset = this.width > 860 ? this.opts.insets ?? { left: 0, right: 0 } : { left: 0, right: 0 };
    this.animateTo(zoomIdentity.translate(inset.left + (this.width - inset.left - inset.right) / 2 - k * n.x!, this.height / 2 - k * n.y!).scale(k), 600);
  }

  zoomBy(factor: number) {
    const t = this.transform, cx = this.width / 2, cy = this.height / 2;
    const k = Math.min(8, Math.max(0.08, t.k * factor));
    this.animateTo(zoomIdentity.translate(cx - (cx - t.x) * k / t.k, cy - (cy - t.y) * k / t.k).scale(k), 250);
  }

  has(id: string) { return this.byId.has(id); }
  position(id: string) { const n = this.byId.get(id); return n ? { x: n.x!, y: n.y! } : null; }

  // -- geometry ------------------------------------------------------------------------
  private radius(n: GraphNode) { return n.shape === 'pill' ? Math.max(n.w ?? 0, n.h ?? 0) / 2 * 0.72 : n.r; }

  /** Pills grow up to 1.6x when zoomed out, so their text stays around 10px on screen. */
  private pillScale() { return Math.min(1.6, Math.max(1, 0.75 / this.transform.k)); }

  private sizePill(n: GraphNode) {
    this.ctx.font = `600 ${PILL_FONT}px ${this.opts.font ?? 'sans-serif'}`;
    let w = this.ctx.measureText(n.label).width;
    if (n.badge) { this.ctx.font = `400 ${PILL_FONT - 1}px ${this.opts.font ?? 'sans-serif'}`; w += 8 + this.ctx.measureText(n.badge).width; }
    n.w = w + PILL_PAD_X * 2; n.h = PILL_H;
  }

  private measure() {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.width = Math.max(1, rect.width); this.height = Math.max(1, rect.height);
    this.canvas.width = Math.round(this.width * dpr); this.canvas.height = Math.round(this.height * dpr);
    this.request();
  }

  private toWorld(clientX: number, clientY: number) {
    const rect = this.canvas.getBoundingClientRect();
    return this.transform.invert([clientX - rect.left, clientY - rect.top]);
  }

  private nodeAt(clientX: number, clientY: number): GraphNode | null {
    const [x, y] = this.toWorld(clientX, clientY);
    const slack = 4 / this.transform.k;
    for (let i = this.nodes.length - 1; i >= 0; i--) {
      const n = this.nodes[i];
      if (n.shape === 'pill') {
        const ps = this.pillScale();
        if (Math.abs(x - n.x!) <= n.w! * ps / 2 + slack && Math.abs(y - n.y!) <= n.h! * ps / 2 + slack) return n;
      } else if ((x - n.x!) ** 2 + (y - n.y!) ** 2 <= (n.r + slack) ** 2) return n;
    }
    return null;
  }

  private linkAt(clientX: number, clientY: number): GraphLink | null {
    const [x, y] = this.toWorld(clientX, clientY);
    let best: GraphLink | null = null, bestD = 6 / this.transform.k;
    for (const l of this.links) {
      const a = this.byId.get(l.source)!, b = this.byId.get(l.target)!;
      const c = this.control(a, b, l.curve ?? 0);
      for (let i = 0; i < 8; i++) {
        const p = quad(a, c, b, i / 8), q = quad(a, c, b, (i + 1) / 8);
        const d = segDist(x, y, p.x, p.y, q.x, q.y);
        if (d < bestD) { bestD = d; best = l; }
      }
    }
    return best;
  }

  private control(a: GraphNode, b: GraphNode, curve: number) {
    const mx = (a.x! + b.x!) / 2, my = (a.y! + b.y!) / 2;
    if (!curve) return { x: mx, y: my };
    const dx = b.x! - a.x!, dy = b.y! - a.y!;
    return { x: mx - dy * curve, y: my + dx * curve };
  }

  // -- interaction ---------------------------------------------------------------------
  private onDown = (event: PointerEvent) => {
    if (event.button) return;
    this.down = { x: event.clientX, y: event.clientY };
    const node = this.nodeAt(event.clientX, event.clientY);
    if (!node) return;
    this.canvas.setPointerCapture(event.pointerId);
    this.dragging = { node, startX: event.clientX, startY: event.clientY, moved: false, wasFixed: !!node.fixed };
  };

  private onMove = (event: PointerEvent) => {
    if (this.dragging) {
      const d = this.dragging;
      if (!d.moved && Math.hypot(event.clientX - d.startX, event.clientY - d.startY) < 4) return;
      if (!d.moved) { d.moved = true; if (!reduceMotion()) this.sim.alphaTarget(0.15).restart(); }
      const [x, y] = this.toWorld(event.clientX, event.clientY);
      d.node.fx = x; d.node.fy = y;
      if (reduceMotion()) { d.node.x = x; d.node.y = y; this.sim.tick(2); }
      this.request();
      return;
    }
    const node = this.nodeAt(event.clientX, event.clientY);
    const link = !node && this.opts.linkHits ? this.linkAt(event.clientX, event.clientY) : null;
    const hit = node ? { node } : link ? { link } : null;
    if (hit?.node !== this.hover?.node || hit?.link !== this.hover?.link) {
      this.hover = hit;
      this.canvas.style.cursor = hit ? 'pointer' : 'grab';
      this.opts.onHover?.(hit);
      this.request();
    }
  };

  private onUp = (event: PointerEvent) => {
    const d = this.dragging;
    this.dragging = null;
    if (d) {
      this.sim.alphaTarget(0);
      if (d.moved) {
        if (!d.wasFixed) { d.node.fx = undefined; d.node.fy = undefined; }
        else { d.node.fx = d.node.x; d.node.fy = d.node.y; }
        this.down = null;
        return;
      }
    }
    if (!this.down || event.type === 'pointercancel') { this.down = null; return; }
    const moved = Math.hypot(event.clientX - this.down.x, event.clientY - this.down.y) >= 4;
    this.down = null;
    if (moved) return;
    const node = this.nodeAt(event.clientX, event.clientY);
    const link = !node && this.opts.linkHits ? this.linkAt(event.clientX, event.clientY) : null;
    this.opts.onClick?.(node ? { node } : link ? { link } : null);
  };

  private onLeave = () => {
    if (this.hover && !this.dragging) { this.hover = null; this.opts.onHover?.(null); this.request(); }
  };

  private animateTo(target: ZoomTransform, duration: number) {
    cancelAnimationFrame(this.animation);
    const from = this.transform;
    if (reduceMotion() || duration <= 0) { this.sel.call(this.zoomer.transform, target); return; }
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration), e = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
      const k = from.k * (target.k / from.k) ** e;
      this.sel.call(this.zoomer.transform, zoomIdentity.translate(from.x + (target.x - from.x) * e, from.y + (target.y - from.y) * e).scale(k));
      if (t < 1) this.animation = requestAnimationFrame(step);
    };
    this.animation = requestAnimationFrame(step);
  }

  // -- drawing -------------------------------------------------------------------------
  private request() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => { this.frame = 0; this.draw(); });
  }

  private draw() {
    const { ctx, transform: t } = this;
    const dpr = this.canvas.width / this.width;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.setTransform(dpr * t.k, 0, 0, dpr * t.k, dpr * t.x, dpr * t.y);

    const focus = this.hover?.node?.id ?? this.selected;
    const focusLink = this.hover?.link ?? null;
    const lit = new Set<string>();
    if (focus && this.byId.has(focus)) { lit.add(focus); for (const o of this.neighbours.get(focus) ?? []) lit.add(o); }
    if (focusLink) { lit.add(focusLink.source); lit.add(focusLink.target); }
    const dimming = lit.size > 0;

    const territories = this.drawRegions(dimming);

    // links
    ctx.lineCap = 'round';
    for (const l of this.links) {
      const a = this.byId.get(l.source)!, b = this.byId.get(l.target)!;
      const on = !dimming || l === focusLink || (focus != null && (l.source === focus || l.target === focus));
      ctx.globalAlpha = (l.opacity ?? 0.7) * (on ? 1 : 0.12) * (on && dimming ? 1.25 : 1);
      ctx.lineWidth = (l.width * (on && dimming ? 1.4 : 1)) / Math.sqrt(t.k);
      if (l.colors) {
        const g = ctx.createLinearGradient(a.x!, a.y!, b.x!, b.y!);
        g.addColorStop(0, l.colors[0]); g.addColorStop(1, l.colors[1]);
        ctx.strokeStyle = g;
      } else ctx.strokeStyle = l.color ?? '#999';
      ctx.setLineDash(l.dash ? l.dash.map(d => d / Math.sqrt(t.k)) : []);
      const c = this.control(a, b, l.curve ?? 0);
      ctx.beginPath();
      ctx.moveTo(a.x!, a.y!);
      ctx.quadraticCurveTo(c.x, c.y, b.x!, b.y!);
      ctx.stroke();
      if (l.arrow) this.arrow(l, a, b, c, t.k);
    }
    ctx.setLineDash([]);

    // nodes (pills keep a readable size when zoomed out)
    const ps = this.pillScale();
    for (const n of this.nodes) {
      ctx.globalAlpha = !dimming || lit.has(n.id) ? 1 : 0.18;
      const pill = n.shape === 'pill' && ps !== 1;
      if (pill) { ctx.save(); ctx.translate(n.x!, n.y!); ctx.scale(ps, ps); ctx.translate(-n.x!, -n.y!); }
      const fill = this.fillStyle(n);
      ctx.fillStyle = fill;
      ctx.strokeStyle = n.stroke ?? 'transparent';
      ctx.lineWidth = (n.strokeWidth ?? 1.5) / Math.sqrt(t.k);
      ctx.beginPath();
      if (n.shape === 'pill') ctx.roundRect(n.x! - n.w! / 2, n.y! - n.h! / 2, n.w!, n.h!, n.h! / 2);
      else if (n.shape === 'square') ctx.rect(n.x! - n.r, n.y! - n.r, n.r * 2, n.r * 2);
      else ctx.arc(n.x!, n.y!, n.r, 0, Math.PI * 2);
      ctx.fill();
      if (n.stroke) ctx.stroke();
      if (n.id === this.selected) {
        ctx.strokeStyle = '#141314'; ctx.lineWidth = 2 / t.k;
        ctx.beginPath();
        if (n.shape === 'pill') ctx.roundRect(n.x! - n.w! / 2 - 4 / t.k, n.y! - n.h! / 2 - 4 / t.k, n.w! + 8 / t.k, n.h! + 8 / t.k, n.h! / 2 + 4 / t.k);
        else ctx.arc(n.x!, n.y!, (n.shape === 'square' ? n.r * 1.42 : n.r) + 4 / t.k, 0, Math.PI * 2);
        ctx.stroke();
      }
      if (n.shape === 'pill') this.pillText(n);
      if (pill) ctx.restore();
    }

    // labels: screen-size text, placed by priority without overlapping each other or any node
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const font = this.opts.font ?? 'sans-serif', halo = this.opts.halo ?? '#fff';
    const placed: Box[] = [], discs: Box[] = [];
    const inView = (sx: number, sy: number, m: number) => sx > -m && sy > -m && sx < this.width + m && sy < this.height + m;
    for (const n of this.nodes) {
      const [sx, sy] = t.apply([n.x!, n.y!]);
      if (!inView(sx, sy, 60)) continue;
      const rx = (n.shape === 'pill' ? n.w! * ps / 2 : n.shape === 'square' ? n.r * 1.2 : n.r) * t.k;
      const ry = n.shape === 'pill' ? n.h! * ps / 2 * t.k : rx;
      if (rx >= 2) discs.push([sx - rx, sy - ry, sx + rx, sy + ry]);
    }
    ctx.lineJoin = 'round';

    // region names first: quiet spaced capitals on the rim of each territory
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = `700 10.5px ${font}`;
    setSpacing(ctx, '1.6px');
    for (const r of territories) {
      const [sx, sy] = t.apply([r.x, r.y]);
      const text = r.label.toUpperCase(), w = ctx.measureText(text).width;
      const box: Box = [sx - w / 2 - 4, sy - 9, sx + w / 2 + 4, sy + 9];
      if (!inView(sx, sy, 0) || overlaps(box, placed)) continue;
      placed.push(box);
      ctx.globalAlpha = dimming ? 0.35 : 0.95;
      ctx.strokeStyle = halo; ctx.lineWidth = 4;
      ctx.strokeText(text, sx, sy);
      ctx.fillStyle = blend([{ color: r.color, weight: 1 }, { color: '#141314', weight: 0.9 }]);
      ctx.fillText(text, sx, sy);
    }
    setSpacing(ctx, '0px');

    const all = t.k >= (this.opts.labelZoom ?? 1.8);
    const candidates = this.nodes.filter(n => n.shape !== 'pill' && (all || n.pinLabel || lit.has(n.id) || (n.priority ?? 0) * t.k > 0.9));
    candidates.sort((a, b) => Number(lit.has(b.id)) - Number(lit.has(a.id)) || Number(!!b.pinLabel) - Number(!!a.pinLabel) || (b.priority ?? 0) - (a.priority ?? 0));
    ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    for (const n of candidates) {
      const [sx, sy] = t.apply([n.x!, n.y!]);
      if (!inView(sx, sy, 200)) continue;
      const strong = lit.has(n.id) || n.pinLabel;
      const size = n.pinLabel ? 14 : 11.5;
      ctx.font = `${strong ? 600 : 400} ${size}px ${font}`;
      const text = n.label.length > 30 && !lit.has(n.id) && !n.pinLabel ? n.label.slice(0, 28).trimEnd() + '…' : n.label;
      const w = ctx.measureText(text).width, r = (n.shape === 'square' ? n.r * 1.2 : n.r) * t.k;
      // below, above, right, left: the first spot that is free wins
      const spots: [number, number][] = [[sx - w / 2, sy + r + 3], [sx - w / 2, sy - r - 3 - size], [sx + r + 5, sy - size / 2], [sx - r - 5 - w, sy - size / 2]];
      let spot = spots.find(([x, y]) => { const b: Box = [x - 2, y - 1, x + w + 2, y + size + 1]; return !overlaps(b, placed) && !overlaps(b, discs); });
      if (!spot) { if (!strong) continue; spot = spots[0]; }
      const [x, y] = spot;
      placed.push([x - 2, y - 1, x + w + 2, y + size + 1]);
      ctx.globalAlpha = !dimming || lit.has(n.id) ? 1 : 0.25;
      ctx.strokeStyle = halo; ctx.lineWidth = 3.5;
      ctx.strokeText(text, x, y);
      ctx.fillStyle = '#141314';
      ctx.fillText(text, x, y);
    }

    // edge labels run along their edge, only where the edge is long enough to carry them
    if (this.opts.showLinkLabels?.()) {
      ctx.font = `400 10px ${font}`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      for (const l of this.links) {
        if (!l.label || (dimming && !(lit.has(l.source) && lit.has(l.target)))) continue;
        const a = this.byId.get(l.source)!, b = this.byId.get(l.target)!;
        const c = this.control(a, b, l.curve ?? 0), m = quad(a, c, b, 0.5);
        const [sx, sy] = t.apply([m.x, m.y]);
        if (!inView(sx, sy, 0)) continue;
        const w = ctx.measureText(l.label).width;
        if ((Math.hypot(b.x! - a.x!, b.y! - a.y!) - this.radius(a) - this.radius(b)) * t.k < w + 18) continue;
        const p = quad(a, c, b, 0.45), q = quad(a, c, b, 0.55);
        let angle = Math.atan2(q.y - p.y, q.x - p.x);
        if (angle > Math.PI / 2) angle -= Math.PI; else if (angle < -Math.PI / 2) angle += Math.PI;
        const cos = Math.abs(Math.cos(angle)), sin = Math.abs(Math.sin(angle));
        const hw = (w * cos + 12 * sin) / 2, hh = (w * sin + 12 * cos) / 2;
        const box: Box = [sx - hw, sy - hh, sx + hw, sy + hh];
        if (overlaps(box, placed) || overlaps(box, discs)) continue;
        placed.push(box);
        ctx.save();
        ctx.translate(sx, sy); ctx.rotate(angle);
        ctx.globalAlpha = 0.9;
        ctx.strokeStyle = halo; ctx.lineWidth = 3;
        ctx.strokeText(l.label, 0, 0);
        ctx.fillStyle = '#554f4d';
        ctx.fillText(l.label, 0, 0);
        ctx.restore();
      }
    }
    ctx.globalAlpha = 1;
  }

  /** Territories: the union of generous discs around each region's nodes, in two soft steps. Returns where to name them. */
  private drawRegions(dimming: boolean) {
    const ctx = this.ctx, names: { label: string; color: string; x: number; y: number }[] = [];
    const members = new Map<string, GraphNode[]>();
    for (const n of this.nodes) if (n.group && this.regions[n.group]) {
      if (!members.has(n.group)) members.set(n.group, []);
      members.get(n.group)!.push(n);
    }
    for (const [id, ms] of members) {
      const region = this.regions[id];
      ctx.fillStyle = region.color;
      for (const [pad, alpha] of [[34, 0.07], [14, 0.08]]) {
        ctx.globalAlpha = alpha * (dimming ? 0.5 : 1);
        ctx.beginPath(); // one path of same-direction arcs fills as a union, without stacking alpha
        for (const m of ms) { const r = this.radius(m) + pad; ctx.moveTo(m.x! + r, m.y!); ctx.arc(m.x!, m.y!, r, 0, Math.PI * 2); }
        ctx.fill();
      }
      // the name sits on the territory's outer rim, facing away from the graph's centre
      let cx = 0, cy = 0;
      for (const m of ms) { cx += m.x!; cy += m.y!; }
      cx /= ms.length; cy /= ms.length;
      const len = Math.hypot(cx, cy), ux = len > 1 ? cx / len : 0, uy = len > 1 ? cy / len : -1;
      const reach = ms.map(m => (m.x! - cx) * ux + (m.y! - cy) * uy + this.radius(m)).sort((a, b) => a - b);
      const rim = reach[Math.floor((reach.length - 1) * 0.85)] + 44;
      names.push({ label: region.label, color: region.color, x: cx + ux * rim, y: cy + uy * rim });
    }
    ctx.globalAlpha = 1;
    return names;
  }

  private fillStyle(n: GraphNode): string | CanvasGradient {
    if (typeof n.fill === 'string') return n.fill;
    if (n.fill.length === 1) return n.fill[0];
    const ctx = this.ctx;
    if (n.shape === 'pill') {
      const g = ctx.createLinearGradient(n.x! - n.w! / 2, 0, n.x! + n.w! / 2, 0);
      n.fill.forEach((c, i) => g.addColorStop(i / (n.fill.length - 1), c));
      return g;
    }
    // colours flow around the node and meet again where they started
    const g = ctx.createConicGradient(-Math.PI / 2, n.x!, n.y!);
    n.fill.forEach((c, i) => g.addColorStop(i / n.fill.length, c));
    g.addColorStop(1, n.fill[0]);
    return g;
  }

  private pillText(n: GraphNode) {
    const ctx = this.ctx, font = this.opts.font ?? 'sans-serif';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    ctx.font = `600 ${PILL_FONT}px ${font}`;
    const lw = ctx.measureText(n.label).width;
    ctx.font = `400 ${PILL_FONT - 1}px ${font}`;
    const bw = n.badge ? ctx.measureText(n.badge).width + 8 : 0;
    let x = n.x! - (lw + bw) / 2;
    ctx.fillStyle = n.textColor ?? '#141314';
    ctx.font = `600 ${PILL_FONT}px ${font}`;
    ctx.fillText(n.label, x, n.y! + 1);
    if (n.badge) {
      x += lw + 8;
      ctx.globalAlpha *= 0.6;
      ctx.font = `400 ${PILL_FONT - 1}px ${font}`;
      ctx.fillText(n.badge, x, n.y! + 1);
    }
  }

  private arrow(l: GraphLink, a: GraphNode, b: GraphNode, c: { x: number; y: number }, k: number) {
    const ctx = this.ctx;
    const r = (b.shape === 'square' ? b.r * 1.3 : b.r) + 1.5;
    const dx = b.x! - c.x, dy = b.y! - c.y, len = Math.hypot(dx, dy) || 1;
    const ux = dx / len, uy = dy / len;
    const tipX = b.x! - ux * r, tipY = b.y! - uy * r;
    const size = Math.max(3, l.width * 1.5) / Math.sqrt(k);
    ctx.setLineDash([]);
    ctx.fillStyle = l.colors ? l.colors[1] : l.color ?? '#999';
    ctx.beginPath();
    ctx.moveTo(tipX, tipY);
    ctx.lineTo(tipX - ux * size - uy * size * 0.4, tipY - uy * size + ux * size * 0.4);
    ctx.lineTo(tipX - ux * size + uy * size * 0.4, tipY - uy * size - ux * size * 0.4);
    ctx.closePath();
    ctx.fill();
    void a;
  }
}

type Box = [number, number, number, number];
const overlaps = (b: Box, list: Box[]) => list.some(p => b[0] < p[2] && b[2] > p[0] && b[1] < p[3] && b[3] > p[1]);
/** canvas letter-spacing where the browser has it */
function setSpacing(ctx: CanvasRenderingContext2D, value: string) {
  if ('letterSpacing' in ctx) (ctx as { letterSpacing: string }).letterSpacing = value;
}

function quad(a:{ x?: number; y?: number }, c: { x: number; y: number }, b: { x?: number; y?: number }, t: number) {
  const u = 1 - t;
  return { x: u * u * a.x! + 2 * u * t * c.x + t * t * b.x!, y: u * u * a.y! + 2 * u * t * c.y + t * t * b.y! };
}

function segDist(x: number, y: number, x1: number, y1: number, x2: number, y2: number) {
  const dx = x2 - x1, dy = y2 - y1, len = dx * dx + dy * dy;
  const t = len ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / len)) : 0;
  return Math.hypot(x - x1 - t * dx, y - y1 - t * dy);
}

/** Average colours in linear RGB, weighted: how a node takes on the colours it connects. */
export function blend(colors: { color: string; weight: number }[]): string {
  let r = 0, g = 0, b = 0, w = 0;
  for (const { color, weight } of colors) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(color);
    if (!m || weight <= 0) continue;
    const lin = (h: string) => (parseInt(h, 16) / 255) ** 2.2;
    r += lin(m[1]) * weight; g += lin(m[2]) * weight; b += lin(m[3]) * weight; w += weight;
  }
  if (!w) return colors[0]?.color ?? '#999999';
  const hex = (v: number) => Math.round((v / w) ** (1 / 2.2) * 255).toString(16).padStart(2, '0');
  return `#${hex(r)}${hex(g)}${hex(b)}`;
}

/** Mix a colour towards white (amount 0..1). */
export function tint(color: string, amount: number) { return blend([{ color, weight: 1 - amount }, { color: '#ffffff', weight: amount }]); }
