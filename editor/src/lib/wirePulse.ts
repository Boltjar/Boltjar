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
