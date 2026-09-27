// ============================================================================
// wirePulse: which drawn wires light while a graph runs. The server says which
// wires a value actually travelled (a `carry` event): a push names the trigger
// wires it fired, a read names the one wire into the node that read it. Only
// those wires light; a source's other wires carried nothing and stay dark.
//
// A carry reaches a wire through this tiny bus, never through React state, so
// an event lights one wire without re-rendering the canvas: each TypedEdge
// subscribes by its own edge id and restarts its own pulse.
// ============================================================================
import { liveEdgeKey } from "./liveClassify";

/** One drawn wire a value travelled: [src, src_port, dst, dst_port]. */
export type CarryWire = readonly [string, string, string, string];

/** The edge ids (graphAdapter's `edgeId`) of the wires a carry names, each once. */
export function carriedEdgeIds(wires: readonly CarryWire[]): string[] {
  const ids: string[] = [];
  for (const w of wires) {
    if (!Array.isArray(w) || w.length !== 4) continue;
    const id = liveEdgeKey(String(w[0]), String(w[1]), String(w[2]), String(w[3]));
    if (!ids.includes(id)) ids.push(id);
  }
  return ids;
}

type Listener = () => void;
const listeners = new Map<string, Set<Listener>>();

/** Call `fn` each time the wire `edgeId` carries a value. Returns the unsubscribe. */
export function onWirePulse(edgeId: string, fn: Listener): () => void {
  let set = listeners.get(edgeId);
  if (!set) listeners.set(edgeId, (set = new Set()));
  set.add(fn);
  return () => {
    set!.delete(fn);
    if (set!.size === 0 && listeners.get(edgeId) === set) listeners.delete(edgeId);
  };
}

/** Light the wires a `carry` event names. Returns how many wires were listening. */
export function pulseWires(wires: readonly CarryWire[]): number {
  let lit = 0;
  for (const id of carriedEdgeIds(wires)) {
    const set = listeners.get(id);
    if (!set) continue;
    lit += 1;
    for (const fn of [...set]) fn();
  }
  return lit;
}

// ============================================================================
// The comet: what a carry looks like on its wire. A near-white head with a long
// soft tail in the wire's type colour slides from the source port to the target
// port along the wire. Each carry launches one; up to COMET_MAX_IN_FLIGHT fly at
// once, so fast carries read as a stream, and a steady 1 s rhythm breathes (a
// comet always lands within COMET_MAX_MS, so the wire is dark between beats).
// With no comet in flight the wire is its normal self. Reduced motion gets a
// plain brief tint of the whole wire instead: nothing travels.
//
// Driven with the Web Animations API on elements this module adds to the edge's
// own <g>, so a carry never re-renders React.
// ============================================================================

/** The comet's layers, tail first, head last: [dash length px, opacity, stroke
 *  width px, % of the wire colour mixed into white]. They share one leading
 *  edge, so shorter, brighter layers stack into a head over a fading tail. */
export const COMET_LAYERS: readonly (readonly [number, number, number, number])[] = [
  [170, 0.22, 7, 80], // a wide faint glow along the tail
  [150, 0.4, 2.6, 75],
  [90, 0.6, 2.6, 60],
  [42, 0.85, 2.8, 45],
  [12, 1, 3.4, 15], // the near-white head
];
/** The longest layer: how far the tail trails the head. */
export const COMET_TAIL = Math.max(...COMET_LAYERS.map((l) => l[0]));
export const COMET_PX_PER_MS = 0.85;
export const COMET_MIN_MS = 420;
/** A comet always lands within this, so a 1 s rhythm is dark between beats. */
export const COMET_MAX_MS = 800;
export const COMET_MAX_IN_FLIGHT = 5;
/** The reduced-motion tint: how long the whole wire takes to fade back. */
export const TINT_MS = 520;
/** The comet group's opacity over its flight: full, then a fade as it lands. */
export const COMET_FADE: Keyframe[] = [{ opacity: 1 }, { opacity: 1, offset: 0.8 }, { opacity: 0 }];

/** How long a comet takes to cross a wire `len` px long, tail included. */
export function cometDuration(len: number): number {
  const ms = (Math.max(0, len) + COMET_TAIL) / COMET_PX_PER_MS;
  return Math.min(COMET_MAX_MS, Math.max(COMET_MIN_MS, ms));
}

/** One layer of a comet on a wire `len` px long, as a single dash: the dash
 *  pattern, and the stroke-dashoffset it starts and ends at. The dash covers
 *  [s - dash, s] along the wire, where s runs from 0 (the head leaves the
 *  source port) to len + COMET_TAIL (the tail has passed the target port). */
export function cometDash(len: number, dash: number): { dasharray: string; from: number; to: number } {
  return {
    dasharray: `${dash} ${Math.max(0, len) + COMET_TAIL + 20}`,
    from: dash,
    to: dash - (Math.max(0, len) + COMET_TAIL),
  };
}

/** The in-flight slots of one wire's comets. A carry takes a free slot, or when
 *  all are flying, the one that lands soonest (the oldest, nearly home), so the
 *  newest carry always shows. Pure: time comes in as `now`. */
export class CometSlots {
  private ends: number[] = [];
  constructor(readonly max: number = COMET_MAX_IN_FLIGHT) {}

  /** The slot a comet launched at `now` for `ms` flies in. */
  take(now: number, ms: number): number {
    let slot = this.ends.findIndex((end) => end <= now);
    if (slot < 0) {
      if (this.ends.length < this.max) slot = this.ends.length;
      else slot = this.ends.indexOf(Math.min(...this.ends));
    }
    this.ends[slot] = now + ms;
    return slot;
  }

  /** How many comets are still flying at `now`. */
  inFlight(now: number): number {
    return this.ends.filter((end) => end > now).length;
  }
}

/** The DOM this module needs (the page's `document`; tests pass a stand-in). */
export interface CometDoc {
  createElementNS(ns: string, tag: string): SVGElement;
}

interface Comet {
  g: SVGElement;
  paths: SVGElement[];
  anims: Animation[];
}

const SVG_NS = "http://www.w3.org/2000/svg";
const mix = (color: string, pct: number) => `color-mix(in srgb, ${color} ${pct}%, white)`;

/** What a wire's comets fly along: its drawn path element and type colour. */
export interface CometSource {
  path: SVGPathElement | null;
  color: string;
}

/** Mount the comets of one wire into `host` (an empty <g> of the edge). `fire`
 *  launches one comet (or the reduced-motion tint); `dispose` stops and removes
 *  everything it added. */
export function mountComets(
  host: SVGElement,
  source: () => CometSource,
  opts: { reduced?: () => boolean; now?: () => number; doc?: CometDoc } = {},
): { fire: () => void; dispose: () => void } {
  const doc: CometDoc = opts.doc ?? document;
  const now = opts.now ?? (() => performance.now());
  const reduced = opts.reduced ?? (() =>
    typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches);
  const slots = new CometSlots();
  const comets: Comet[] = [];
  let tint: SVGElement | null = null;
  let tintAnim: Animation | null = null;

  const line = (parent: SVGElement): SVGElement => {
    const p = doc.createElementNS(SVG_NS, "path");
    p.setAttribute("fill", "none");
    p.setAttribute("stroke-linecap", "round");
    parent.appendChild(p);
    return p;
  };

  const newComet = (): Comet => {
    const g = doc.createElementNS(SVG_NS, "g");
    g.setAttribute("class", "wf-edge-comet");
    g.setAttribute("visibility", "hidden");
    host.appendChild(g);
    const paths = COMET_LAYERS.map(([, opacity, width]) => {
      const p = line(g);
      p.setAttribute("stroke-width", String(width));
      p.setAttribute("stroke-opacity", String(opacity));
      return p;
    });
    return { g, paths, anims: [] };
  };

  const fire = () => {
    const { path, color } = source();
    const d = path?.getAttribute("d");
    if (!path || !d) return;
    if (reduced()) {
      // a plain brief tint of the whole wire: nothing travels
      if (!tint) {
        tint = line(host);
        tint.setAttribute("class", "wf-edge-tint");
        tint.setAttribute("stroke-width", "2.6");
        tint.setAttribute("opacity", "0");
      }
      tint.setAttribute("d", d);
      tint.style.stroke = mix(color, 60);
      tintAnim?.cancel();
      tintAnim = tint.animate([{ opacity: 0.9 }, { opacity: 0 }], { duration: TINT_MS, easing: "ease-in", fill: "forwards" });
      return;
    }
    const len = path.getTotalLength();
    const ms = cometDuration(len);
    const slot = slots.take(now(), ms);
    const comet = (comets[slot] ??= newComet());
    for (const a of comet.anims) a.cancel(); // a recycled comet starts over
    comet.anims = comet.paths.map((p, i) => {
      const [dash, , , pct] = COMET_LAYERS[i];
      const { dasharray, from, to } = cometDash(len, dash);
      p.setAttribute("d", d);
      p.setAttribute("stroke-dasharray", dasharray);
      p.style.stroke = mix(color, pct);
      return p.animate([{ strokeDashoffset: from }, { strokeDashoffset: to }], { duration: ms, easing: "linear", fill: "forwards" });
    });
    const fade = comet.g.animate(COMET_FADE, { duration: ms, fill: "forwards" });
    comet.anims.push(fade);
    comet.g.setAttribute("visibility", "visible");
    fade.onfinish = () => {
      // a finish that was already on its way when this comet was relaunched
      // belongs to the old flight: it must not stop the new one
      if (comet.anims[comet.anims.length - 1] !== fade) return;
      // landed: back to the wire's normal look, with no animation left running
      for (const a of comet.anims) a.cancel();
      comet.anims = [];
      comet.g.setAttribute("visibility", "hidden");
    };
  };

  const dispose = () => {
    for (const c of comets) {
      for (const a of c.anims) a.cancel();
      c.g.remove();
    }
    comets.length = 0;
    tintAnim?.cancel();
    tint?.remove();
    tint = null;
  };

  return { fire, dispose };
}
