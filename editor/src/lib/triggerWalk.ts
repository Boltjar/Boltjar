// ============================================================================
// The wires of a saved graph, and the trigger behind any output of it. Mirrors
// TriggerWalk in boltjar/graph_format.py: the format 3 migration and the heal
// of a data wire into a trigger both read it, on the server and here alike.
// ============================================================================
import type { GraphEdge, GraphNode, NodeDef } from "../types/protocol";

export type PortRef = [string, string];

const ROUTER_TYPE = "core.flow.router";
export const WIRELESS_IN_TYPE = "core.flow.wireless_in";
export const WIRELESS_OUT_TYPE = "core.flow.wireless_out";

/** A Wireless node's channel, read the way the runtime reads it. */
function channelOf(node: GraphNode): string {
  const config = node.config && typeof node.config === "object" ? node.config : {};
  return String((config as Record<string, unknown>).channel ?? "1");
}

/** One key per (node, port). */
export const keyOf = (node: string, port: string) => `${node}\u0000${port}`;

type Plan =
  | { done: PortRef | null }
  | { deps: PortRef[]; combine: (found: (PortRef | null)[]) => PortRef | null };

const ACTIVE = Symbol("active");

/**
 * triggerFor(src, port) is the trigger that fires when (src, port) carries a
 * new value: the port itself when it is an event, else the node's `trigger`
 * output, else its one event output. Through a Router, a bypassed node or a
 * Wireless pair it is found at the real source; for a data node with no event
 * output it is the one trigger behind all of its wired inputs. null when it
 * cannot be told, a cycle included.
 *
 * The walk is iterative and memoized over the whole graph: each (node, port) is
 * worked out once, so a graph of any size or shape (an opened file can be
 * anything) is walked in time linear in its wires and never recurses.
 */
export class TriggerWalk {
  readonly byId: Map<string, GraphNode>;
  /** each wired input -> its source, the last wire winning as on the server */
  readonly into = new Map<string, PortRef>();
  /** node -> [its input port, its source] */
  readonly inputs = new Map<string, [string, PortRef][]>();
  private readonly wirelessIn = new Map<string, string>();
  private readonly memo = new Map<string, PortRef | null | typeof ACTIVE>();

  constructor(
    nodes: readonly GraphNode[],
    edges: readonly GraphEdge[],
    private readonly defs: ReadonlyMap<string, NodeDef>,
  ) {
    this.byId = new Map(nodes.map((n) => [n.id, n]));
    const dsts = new Map<string, PortRef>();
    for (const e of edges) {
      this.into.set(keyOf(e.dst, e.dst_port), [e.src, e.src_port]);
      dsts.set(keyOf(e.dst, e.dst_port), [e.dst, e.dst_port]);
    }
    for (const [k, [dst, port]] of dsts) {
      const list = this.inputs.get(dst) ?? [];
      list.push([port, this.into.get(k)!]);
      this.inputs.set(dst, list);
    }
    for (const n of nodes) {
      if (n.type === WIRELESS_IN_TYPE && !this.wirelessIn.has(channelOf(n))) this.wirelessIn.set(channelOf(n), n.id);
    }
  }

  def(id: string): NodeDef | undefined {
    return this.defs.get(this.byId.get(id)?.type ?? "");
  }

  /** The wire a pass-through output (a Router, a bypassed node, a Wireless Out)
   *  carries on, null when it carries nothing; false when (src, port) is no
   *  pass-through. */
  upstream(src: string, port: string): PortRef | null | false {
    const node = this.byId.get(src);
    if (!node) return null;
    if (node.type === WIRELESS_OUT_TYPE) {
      const wi = this.wirelessIn.get(channelOf(node));
      return (wi && this.into.get(keyOf(wi, port))) || null;
    }
    if (node.disabled || node.type === ROUTER_TYPE) {
      const def = this.def(src);
      const inPort = Object.entries(def?.bypass ?? {}).find(([, o]) => o === port)?.[0];
      return (inPort && this.into.get(keyOf(src, inPort))) || null;
    }
    return false;
  }

  /** The real (node, port) behind an output, through any pass-throughs, or
   *  null when the wire carries nothing. */
  source(src: string, port: string): PortRef | null {
    const seen = new Set<string>();
    let at: PortRef = [src, port];
    while (!seen.has(keyOf(at[0], at[1]))) {
      seen.add(keyOf(at[0], at[1]));
      const up = this.upstream(at[0], at[1]);
      if (up === false) return at;
      if (up === null) return null;
      at = up;
    }
    return null; // a pass-through cycle carries nothing
  }

  private plan(src: string, port: string): Plan {
    const node = this.byId.get(src);
    if (!node) return { done: null };
    const up = this.upstream(src, port);
    if (up === null) return { done: null };
    if (up !== false) {
      if (node.type !== WIRELESS_OUT_TYPE) return { deps: [up], combine: ([found]) => found };
      const wi = this.wirelessIn.get(channelOf(node))!;
      return {
        deps: [up],
        combine: ([found]) => {
          // the same trigger through this pair, when the Wireless In carries it
          if (!found) return null;
          const socket = (this.inputs.get(wi) ?? []).find(([, s]) => s[0] === found[0] && s[1] === found[1]);
          return socket ? [src, socket[0]] : found;
        },
      };
    }
    const def = this.def(src);
    if (!def) return { done: null };
    const outType = (name: string) => def.outputs.find((p) => p.name === name)?.type;
    if (outType(port) === "event") return { done: [src, port] };
    if (outType("trigger") === "event") return { done: [src, "trigger"] };
    const events = def.outputs.filter((p) => p.type === "event" && !p.optional).map((p) => p.name);
    if (events.length) return { done: events.length === 1 ? [src, events[0]] : null };
    return {
      deps: (this.inputs.get(src) ?? []).map(([, s]) => s),
      combine: (found) => {
        const distinct = new Map<string, PortRef>();
        for (const f of found) if (f) distinct.set(keyOf(f[0], f[1]), f);
        return distinct.size === 1 ? [...distinct.values()][0] : null;
      },
    };
  }

  triggerFor(src: string, port: string): PortRef | null {
    const memo = this.memo;
    const root = keyOf(src, port);
    const stack: { ref: PortRef; plan?: Plan }[] = [{ ref: [src, port] }];
    while (stack.length) {
      const frame = stack[stack.length - 1];
      const k = keyOf(frame.ref[0], frame.ref[1]);
      if (!frame.plan) {
        if (memo.has(k)) { // worked out, or under way higher up (a cycle)
          stack.pop();
          continue;
        }
        const plan = this.plan(frame.ref[0], frame.ref[1]);
        if ("done" in plan) {
          memo.set(k, plan.done);
          stack.pop();
          continue;
        }
        memo.set(k, ACTIVE);
        frame.plan = plan;
        for (const dep of plan.deps) if (!memo.has(keyOf(dep[0], dep[1]))) stack.push({ ref: dep });
        continue;
      }
      const plan = frame.plan as Extract<Plan, { deps: PortRef[] }>;
      const found = plan.deps.map((d) => {
        const v = memo.get(keyOf(d[0], d[1]));
        return v === ACTIVE || v === undefined ? null : v;
      });
      memo.set(k, plan.combine(found));
      stack.pop();
    }
    const result = memo.get(root);
    return result === ACTIVE || result === undefined ? null : result;
  }
}
