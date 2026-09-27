// ============================================================================
// Dead-wire healing: the editor removes, by itself, every wire whose source or
// target handle is not a port its node actually has, so a broken wire the
// system left behind (a rename that re-pointed a handle, a port a model or a
// key list no longer has, a node that is gone) never becomes the user's chore.
// App runs it on every graph load (the server graph, a localStorage draft, a
// tab switch) and reports each removal on the console.
//
// A handle is judged by the same source of truth the canvas renders:
// concreteInputs / concreteOutputs (lib/dynamicPorts), given the node's
// connected inputs and the Wireless channel map, so every growable or dynamic
// socket the canvas can draw (Template tags, HTTP / KV / DB tags, the LLM's
// `tools`, Wireless sockets) is alive. An output the node's definition declares
// is alive too while a knob hides it (a DB `rows` under `insert`, the LLM's
// `reasoning` with thinking off), and so is an input declared for some
// operations (a Vectors `embedding` under `clear`): the port still exists, the
// knob brings it back.
//
// SAFETY: a wire is judged only when each of its nodes is fully known: its
// definition is loaded and, for a model-driven node (LLM / TTS / STT / Embed /
// Rerank), so is the manifest of its selected model. Until then the node's
// ports are not final (an LLM without its manifest shows no image, audio or
// tools port), so its wires are left untouched rather than wiped.
// ============================================================================
import type { Graph, GraphEdge, GraphNode, ModelManifest, NodeDef } from "../types/protocol";
import {
  concreteInputs,
  concreteOutputs,
  isGhostHandle,
  modelWidgetOf,
  WIRELESS_IN_ID,
  type WirelessSocket,
} from "./dynamicPorts";

/** Why a wire was removed: the port it touches is gone, or its whole node is. */
export type DeadWireReason = "port" | "node";

/** One wire the heal removed, and why. */
export interface RemovedWire {
  edge: GraphEdge;
  reason: DeadWireReason;
}

/** A healed graph and the wires taken out of it, in the order they were found. */
export interface HealResult {
  graph: Graph;
  removed: RemovedWire[];
}

/** The console line reporting one removed wire. */
export function deadWireNotice({ edge, reason }: RemovedWire): string {
  const gone = reason === "node" ? "a node" : "a port";
  return `removed a wire to ${gone} that no longer exists: `
    + `${edge.src}.${edge.src_port} -> ${edge.dst}.${edge.dst_port}`;
}

/**
 * Remove the dead wires from a graph. Returns the graph itself when nothing is
 * dead, else a copy without them; the input is never mutated. Judging repeats
 * until a pass removes nothing, because removing a Wireless In socket also
 * removes the Wireless Out port that mirrors it.
 */
export function healDeadWires(
  graph: Graph,
  defs: ReadonlyMap<string, NodeDef>,
  models: ReadonlyMap<string, ModelManifest>,
): HealResult {
  const nodes = new Map((graph.nodes ?? []).map((n) => [n.id, n]));
  const removed: RemovedWire[] = [];
  let edges = graph.edges ?? [];
  for (;;) {
    const deadReason = wireJudge(nodes, edges, defs, models);
    const dead = edges.flatMap((edge) => {
      const reason = deadReason(edge);
      return reason ? [{ edge, reason }] : [];
    });
    if (dead.length === 0) break;
    const gone = new Set(dead.map((w) => w.edge));
    edges = edges.filter((e) => !gone.has(e));
    removed.push(...dead);
  }
  return removed.length ? { graph: { ...graph, edges }, removed } : { graph, removed };
}

/**
 * The judge for one set of wires: why a wire is dead, or null when it is alive
 * or cannot be judged yet. Each node's ports are derived exactly as the canvas
 * derives them from those same wires.
 */
function wireJudge(
  nodes: ReadonlyMap<string, GraphNode>,
  edges: readonly GraphEdge[],
  defs: ReadonlyMap<string, NodeDef>,
  models: ReadonlyMap<string, ModelManifest>,
): (e: GraphEdge) => DeadWireReason | null {
  // node id -> the handles wired into it (they grow its growable sockets).
  const connected = new Map<string, Set<string>>();
  for (const e of edges) {
    if (!e.dst_port) continue;
    if (!connected.has(e.dst)) connected.set(e.dst, new Set());
    connected.get(e.dst)!.add(e.dst_port);
  }
  const channels = wirelessChannels(nodes, edges);

  // the node's definition, once everything its ports depend on is loaded.
  const loadedDef = (node: GraphNode): NodeDef | undefined => {
    const def = defs.get(node.type);
    if (!def) return undefined;
    const picker = modelWidgetOf(def);
    if (picker && !models.has(String(node.config?.[picker.name] ?? ""))) return undefined;
    return def;
  };
  // a declared input is a port of the node even while its operation hides it
  // (a Vectors `embedding` under clear).
  const hasInput = (node: GraphNode, def: NodeDef, handle: string): boolean =>
    def.inputs.some((p) => p.name === handle && !!p.op_field)
    || concreteInputs(def, node.config ?? {}, connected.get(node.id) ?? new Set<string>(), models)
      .some((p) => p.name === handle);
  // a declared output is a port of the node even while a knob hides it.
  const hasOutput = (node: GraphNode, def: NodeDef, handle: string): boolean =>
    def.outputs.some((p) => p.name === handle)
    || concreteOutputs(def, node.config ?? {}, models, channels).some((p) => p.name === handle);

  return (e) => {
    const src = nodes.get(e.src);
    const dst = nodes.get(e.dst);
    const srcDef = src && loadedDef(src);
    const dstDef = dst && loadedDef(dst);
    // a wire on a node whose ports are not final yet is never judged.
    if ((src && !srcDef) || (dst && !dstDef)) return null;
    if (!src || !dst || !srcDef || !dstDef) return "node";
    return hasOutput(src, srcDef, e.src_port) && hasInput(dst, dstDef, e.dst_port) ? null : "port";
  };
}

/**
 * channel -> the sockets its Wireless In broadcasts, which every Wireless Out on
 * that channel mirrors as its output ports. The canvas's rule (App's
 * wirelessChannels): the first Wireless In on a channel owns it, and each wire
 * into it is one socket, named by its handle.
 */
function wirelessChannels(
  nodes: ReadonlyMap<string, GraphNode>,
  edges: readonly GraphEdge[],
): Map<string, WirelessSocket[]> {
  const owner = new Map<string, string>();
  const channelOf = new Map<string, string>();
  for (const n of nodes.values()) {
    if (n.type !== WIRELESS_IN_ID) continue;
    const ch = String(n.config?.channel ?? "1");
    channelOf.set(n.id, ch);
    if (!owner.has(ch)) owner.set(ch, n.id);
  }
  const channels = new Map<string, WirelessSocket[]>();
  for (const e of edges) {
    const ch = channelOf.get(e.dst);
    if (ch === undefined || owner.get(ch) !== e.dst || isGhostHandle(e.dst_port)) continue;
    if (!channels.has(ch)) channels.set(ch, []);
    channels.get(ch)!.push({ name: e.dst_port, type: "any", src: e.src, srcPort: e.src_port });
  }
  return channels;
}
