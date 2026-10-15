// ============================================================================
// Adapter between the backend Graph shape and React Flow's node/edge model.
// The editor's source of truth is the backend Graph (nodes with config+pos,
// edges with src/dst port names). React Flow nodes/edges are a *projection* of
// it; this module is the only place that knows both shapes.
// ============================================================================
import type { Edge, Node, XYPosition } from "@xyflow/react";
import type { Graph, GraphEdge, GraphNode, ModelManifest, NodeDef } from "../types/protocol";
import { DB_ID, KV_ID, LLM_ID, llmInputType, llmOutputType, nodePromoted, promotedParamType, widgetPortType } from "./dynamicPorts";

/** Data carried on each React Flow node: enough to render the card. */
export interface WFNodeData {
  /** the node instance id (same as RF node id) */
  instanceId: string;
  /** the NodeDef id (backend `type`) */
  typeId: string;
  config: Record<string, unknown>;
  [key: string]: unknown;
}

export type WFNode = Node<WFNodeData, "workflow">;

/** Data carried on each React Flow edge: the source type drives the colour. */
export interface WFEdgeData {
  type: string; // source-port data type
  [key: string]: unknown;
}

export type WFEdge = Edge<WFEdgeData>;

/** Deterministic edge id from its four endpoints. */
export function edgeId(e: GraphEdge): string {
  return `${e.src}:${e.src_port}->${e.dst}:${e.dst_port}`;
}

/**
 * Resolve the data type of an output port on a node instance. The LLM reshapes
 * its outputs to the selected model (text/audio/image + tool_call), so pass the
 * models map + the node config to resolve those; other nodes ignore them.
 */
export function outputType(
  defs: Map<string, NodeDef>,
  typeId: string,
  port: string,
  models?: ReadonlyMap<string, ModelManifest>,
  config?: Record<string, unknown>,
): string {
  const def = defs.get(typeId);
  if (!def) return "any";
  if (def.id === LLM_ID && models && config) {
    return llmOutputType(def, config, port, models) ?? "any";
  }
  const found = def.outputs.find((p) => p.name === port);
  return found?.type ?? "any";
}

/**
 * Resolve the data type of an input port (handle id) on a node instance. A
 * growable base's materialised sockets (Template tags, Compute value0…) carry
 * the base port's type, so an unknown handle falls back to the lone growable
 * base type, then to "any". The LLM reshapes its inputs to the selected model
 * (modality + promoted-param ports), so pass the models map + node config.
 */
export function inputType(
  defs: Map<string, NodeDef>,
  typeId: string,
  handle: string,
  models?: ReadonlyMap<string, ModelManifest>,
  config?: Record<string, unknown>,
): string {
  const def = defs.get(typeId);
  if (!def) return "any";
  if (def.id === LLM_ID && models && config) {
    return llmInputType(def, config, handle, models) ?? "any";
  }
  const exact = def.inputs.find((p) => p.name === handle);
  if (exact) return exact.type;
  // a promoted widget (universal knob promotion) carries its widget's port type,
  // and a promoted model setting (a TTS `speed`) its param's.
  if (config && nodePromoted(config).includes(handle)) {
    const widget = def.widgets.find((w) => w.name === handle);
    if (widget) return widgetPortType(widget.kind);
    const param = promotedParamType(def, config, handle, models);
    if (param) return param;
  }
  // a KV / DB tag socket (not a declared port) carries `any` (matches the
  // growable `tag` port; the template substitution coerces to str anyway).
  if (def.id === KV_ID || def.id === DB_ID) return "any";
  const growable = def.inputs.find((p) => p.growable);
  return growable?.type ?? "any";
}

/** Node types whose body is a text block and so can be resized (width + height).
 *  They get a default size so the card has stable dimensions to fill. */
export const RESIZABLE_TYPES = new Set(["core.value.text", "core.data.template", "core.data.compute", "core.output.preview", "core.output.chat"]);
export const DEFAULT_SIZE: Record<string, [number, number]> = {
  "core.value.text": [290, 248],
  "core.data.template": [300, 280],
  "core.data.compute": [284, 196],
  "core.output.preview": [290, 280],
  "core.output.chat": [320, 300],
  "core.flow.router": [64, 34],
};

/** Project a backend GraphNode into a React Flow node. */
export function toRFNode(gn: GraphNode): WFNode {
  const node: WFNode = {
    id: gn.id,
    type: "workflow",
    position: { x: gn.pos[0], y: gn.pos[1] },
    data: { instanceId: gn.id, typeId: gn.type, config: gn.config ?? {} },
  };
  // apply a saved size only if it is sane; a stored size below the universal floor
  // (an old graph, or a bad duplicate) is ignored so the node content-sizes instead
  // of rendering collapsed. The Router is a deliberately small pill, exempt from the
  // floor. The live NodeResizer enforces the real per-node min.
  const sane = gn.size && gn.size.length === 2 &&
    (gn.type === "core.flow.router" ? gn.size[0] >= 60 : (gn.size[0] >= 200 && gn.size[1] >= 110));
  if (sane && gn.size) {
    node.width = gn.size[0];
    node.height = gn.size[1];
  } else if (DEFAULT_SIZE[gn.type]) {
    [node.width, node.height] = DEFAULT_SIZE[gn.type];
  }
  return node;
}

/** Project a backend GraphEdge into a React Flow edge, colouring by source type. */
export function toRFEdge(
  ge: GraphEdge,
  defs: Map<string, NodeDef>,
  nodeTypeById: Map<string, string>,
  models?: ReadonlyMap<string, ModelManifest>,
  configById?: Map<string, Record<string, unknown>>,
): WFEdge {
  const srcType = nodeTypeById.get(ge.src) ?? "";
  return {
    id: edgeId(ge),
    source: ge.src,
    sourceHandle: ge.src_port,
    target: ge.dst,
    targetHandle: ge.dst_port,
    type: "typed",
    // only the destination end re-routes (onReconnect); grabbing the source end
    // is a no-op; start a fresh wire from the output handle instead.
    reconnectable: "target",
    data: { type: outputType(defs, srcType, ge.src_port, models, configById?.get(ge.src)) },
  };
}

/** Build the full React Flow projection of a graph. */
export function projectGraph(
  graph: Graph,
  defs: Map<string, NodeDef>,
  models?: ReadonlyMap<string, ModelManifest>,
): { nodes: WFNode[]; edges: WFEdge[] } {
  const nodeTypeById = new Map(graph.nodes.map((n) => [n.id, n.type]));
  const configById = new Map(graph.nodes.map((n) => [n.id, n.config ?? {}]));
  return {
    nodes: graph.nodes.map(toRFNode),
    edges: graph.edges.map((e) => toRFEdge(e, defs, nodeTypeById, models, configById)),
  };
}

/** The graph format this editor writes: the shape serializeGraph produces. It
 *  equals the server's CURRENT_FORMAT (boltjar/graph_format.py, which a test
 *  checks), and the server migrates every graph it serves to that format. */
export const GRAPH_FORMAT = 3;

/** The model value format 1 had for "run whatever model can run now". It is
 *  gone: a model can cost money, so only a person picks one. */
const FORMAT_1_AUTO = "auto";

/**
 * A graph the editor holds from before GRAPH_FORMAT (a localStorage draft or an
 * opened file never passes through the server) in the current format, the way
 * the server's migrate (boltjar/graph_format.py) turns it: format 2 drops the
 * "auto" model, so a model picker that held it holds none; format 3 wires the
 * trigger each node that used to fire on data now fires on (migrateTriggers).
 * `defs` are the node definitions the format 3 step reads. `changed` says
 * whether anything changed. A current graph comes back as is; the input is
 * never mutated.
 */
export function migrateGraph(
  graph: Graph,
  defs: ReadonlyMap<string, NodeDef>,
): { graph: Graph; changed: boolean } {
  const format = graph.format ?? 0;
  if (format >= GRAPH_FORMAT) return { graph, changed: false };
  let changed = false;
  let nodes = graph.nodes;
  let edges = graph.edges;
  if (format < 2) {
    nodes = nodes.map((n) => {
      if (n.config?.model !== FORMAT_1_AUTO) return n;
      changed = true;
      const config = { ...n.config };
      delete config.model;
      return { ...n, config };
    });
  }
  if (format < 3) {
    edges = migrateTriggers(nodes, edges, defs);
    changed = changed || JSON.stringify(edges) !== JSON.stringify(graph.edges);
  }
  return { graph: { ...graph, format: GRAPH_FORMAT, nodes, edges }, changed };
}

/** Format 3: a data wire never fires a node. These nodes used to fire on a data
 *  input and now fire on an event input, reading that data when it fires:
 *  {node type: [data input, its new trigger input]}. The Queue is reshaped by
 *  its own rule (see migrateTriggers). Mirrors FIRED_BY_DATA in
 *  boltjar/graph_format.py. */
const FIRED_BY_DATA: Record<string, [string, string]> = {
  "core.output.log": ["in", "trigger"],
  "core.output.preview": ["in", "trigger"],
  "core.logic.condition": ["value", "trigger"],
  "core.ai.tool": ["result", "result_trigger"],
  "core.ai.tool_args": ["call", "trigger"],
};
const QUEUE_ID = "core.flow.queue";
const LOGIC_ID = "core.logic.condition";
const ROUTER_TYPE = "core.flow.router";
const WIRELESS_IN_TYPE = "core.flow.wireless_in";
const WIRELESS_OUT_TYPE = "core.flow.wireless_out";

type PortRef = [string, string];

/** Whether `port` of a node of `def` is a trigger input (format 3 shape): a
 *  declared port says so itself; any other socket belongs to the growable
 *  input it falls under (the only one, else the one its name starts with), and
 *  a knob promoted to an input is never one. Mirrors NodeSpec.growable_base. */
function firesOn(def: NodeDef | undefined, config: Record<string, unknown> | undefined, port: string): boolean {
  if (!def) return false;
  const declared = def.inputs.find((p) => p.name === port);
  if (declared) return declared.trigger;
  const bases = def.inputs.filter((p) => p.growable);
  if (!bases.length || nodePromoted(config ?? {}).includes(port)) return false;
  const base = bases.length === 1 ? bases[0] : bases.find((p) => port.startsWith(p.name));
  return !!base?.trigger;
}

/**
 * The format 2 -> 3 step over a graph's wires. Mirrors migrate_triggers in
 * boltjar/graph_format.py; the shared cases in
 * editor/test/fixtures/migrate-format-3.json keep the two in step.
 *
 * - The Log, Preview, Logic, Tool and Tool Args fire on a new event input
 *   (FIRED_BY_DATA) and read the data input that used to fire them. The new
 *   trigger is wired from the trigger that came with that data (triggerFor).
 * - A Logic `true` / `false` wire into a data input carried the value: it comes
 *   from the Logic's `value` output now (`true` / `false` are events).
 * - A Queue fires on growable `trigger` sockets and reads its items from
 *   growable `in` sockets: each old socket fed by an event becomes `trigger<k>`;
 *   one fed by data becomes `in<k>`, with `trigger<k>` wired from that data's
 *   trigger. A Queue `out` wire into a trigger input comes from the Queue's
 *   `trigger` output now.
 *
 * A trigger that cannot be told stays unwired: the graph loads, and validation
 * names the trigger, so the person wires it.
 */
export function migrateTriggers(
  nodes: readonly GraphNode[],
  edges: readonly GraphEdge[],
  defs: ReadonlyMap<string, NodeDef>,
): GraphEdge[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const key = (node: string, port: string) => `${node}\u0000${port}`;
  // each wired input -> its source, the last wire winning as on the server
  const into = new Map<string, PortRef>();
  const inputs = new Map<string, PortRef>(); // the same keys -> [dst, dst_port]
  for (const e of edges) {
    into.set(key(e.dst, e.dst_port), [e.src, e.src_port]);
    inputs.set(key(e.dst, e.dst_port), [e.dst, e.dst_port]);
  }
  const intoNode = (id: string): [string, PortRef][] =>
    [...inputs].filter(([, [dst]]) => dst === id).map(([k, [, port]]) => [port, into.get(k)!]);
  const wirelessIn = new Map<string, string>();
  for (const n of nodes) {
    const channel = String(n.config?.channel ?? "1");
    if (n.type === WIRELESS_IN_TYPE && !wirelessIn.has(channel)) wirelessIn.set(channel, n.id);
  }
  const defOf = (id: string) => defs.get(byId.get(id)?.type ?? "");
  const outType = (def: NodeDef | undefined, port: string) => def?.outputs.find((p) => p.name === port)?.type;

  /** The trigger that fires when (src, port) carries a new value; see
   *  trigger_for in boltjar/graph_format.py. */
  const triggerFor = (src: string, port: string, seen: ReadonlySet<string> = new Set()): PortRef | null => {
    const node = byId.get(src);
    if (!node || seen.has(key(src, port))) return null;
    const next = new Set(seen).add(key(src, port));
    const def = defOf(src);
    if (node.type === WIRELESS_OUT_TYPE) {
      const wi = wirelessIn.get(String(node.config?.channel ?? "1"));
      const up = wi ? into.get(key(wi, port)) : undefined;
      const found = up ? triggerFor(up[0], up[1], next) : null;
      if (!found) return null;
      const socket = intoNode(wi!).find(([, s]) => s[0] === found[0] && s[1] === found[1]);
      return socket ? [src, socket[0]] : found;
    }
    if (!def) return null;
    if (node.disabled || node.type === ROUTER_TYPE) {
      const inPort = Object.entries(def.bypass ?? {}).find(([, o]) => o === port)?.[0];
      const up = inPort ? into.get(key(src, inPort)) : undefined;
      return up ? triggerFor(up[0], up[1], next) : null;
    }
    if (outType(def, port) === "event") return [src, port];
    if (outType(def, "trigger") === "event") return [src, "trigger"];
    const events = def.outputs.filter((p) => p.type === "event" && !p.optional).map((p) => p.name);
    if (events.length) return events.length === 1 ? [src, events[0]] : null;
    const found = new Map<string, PortRef>();
    for (const [, [s, p]] of intoNode(src)) {
      const t = triggerFor(s, p, next);
      if (t) found.set(key(t[0], t[1]), t);
    }
    return found.size === 1 ? [...found.values()][0] : null;
  };

  // every decision reads the graph as saved; the changes are applied after
  const renames = new Map<number, Partial<GraphEdge>>();
  const added: GraphEdge[] = [];
  const wired = new Set(into.keys());
  const add = (trigger: PortRef | null, dst: string, port: string) => {
    if (!trigger || wired.has(key(dst, port))) return;
    wired.add(key(dst, port));
    added.push({ src: trigger[0], src_port: trigger[1], dst, dst_port: port });
  };

  for (const n of nodes) {
    const pair = FIRED_BY_DATA[n.type];
    const src = pair ? into.get(key(n.id, pair[0])) : undefined;
    if (pair && src) add(triggerFor(src[0], src[1]), n.id, pair[1]);
  }
  edges.forEach((e, i) => {
    const dst = byId.get(e.dst);
    if (!dst || dst.type === QUEUE_ID) return; // a Queue's own sockets: below
    const srcType = byId.get(e.src)?.type;
    const toTrigger = firesOn(defs.get(dst.type), dst.config, e.dst_port);
    if (srcType === LOGIC_ID && (e.src_port === "true" || e.src_port === "false") && !toTrigger) {
      renames.set(i, { src_port: "value" });
    } else if (srcType === QUEUE_ID && e.src_port === "out" && toTrigger) {
      renames.set(i, { src_port: "trigger" });
    }
  });
  for (const n of nodes) {
    if (n.type !== QUEUE_ID) continue;
    const promoted = new Set(nodePromoted(n.config ?? {}));
    let k = 0;
    edges.forEach((e, i) => {
      if (e.dst !== n.id || e.dst_port === "ack" || promoted.has(e.dst_port)) return;
      while (wired.has(key(n.id, `trigger${k}`)) || wired.has(key(n.id, `in${k}`))) k += 1;
      const trigger = triggerFor(e.src, e.src_port);
      if (trigger && trigger[0] === e.src && trigger[1] === e.src_port) {
        renames.set(i, { dst_port: `trigger${k}` });
        wired.add(key(n.id, `trigger${k}`));
      } else {
        renames.set(i, { dst_port: `in${k}` });
        wired.add(key(n.id, `in${k}`));
        add(trigger, n.id, `trigger${k}`);
      }
      k += 1;
    });
  }
  return [...edges.map((e, i) => (renames.has(i) ? { ...e, ...renames.get(i) } : e)), ...added];
}

/** Reconstruct a backend Graph from the live React Flow state. */
export function serializeGraph(
  name: string | undefined,
  nodes: WFNode[],
  edges: WFEdge[],
  disabledIds: Set<string> = new Set(),
  groups: import("../types/protocol").NodeGroup[] = [],
): Graph {
  return {
    format: GRAPH_FORMAT,
    name,
    ...(groups.length ? { groups: groups.map((g) => ({ ...g, members: [...g.members] })) } : {}),
    nodes: nodes.map<GraphNode>((n) => ({
      id: n.id,
      type: n.data.typeId,
      config: n.data.config,
      pos: [Math.round(n.position.x), Math.round(n.position.y)],
      ...(typeof n.width === "number" && typeof n.height === "number"
        ? { size: [Math.round(n.width), Math.round(n.height)] as [number, number] }
        : {}),
      ...(disabledIds.has(n.id) ? { disabled: true } : {}),
    })),
    edges: edges.map<GraphEdge>((e) => ({
      src: e.source,
      src_port: e.sourceHandle ?? "",
      dst: e.target,
      dst_port: e.targetHandle ?? "",
    })),
  };
}

/** A fresh, unique instance id for a newly added node of a given type. The
 *  first one of a kind gets the bare name ("tts", "llm"); duplicates count up
 *  as " (2)", " (3)" so a lone node never reads "tts1". */
export function freshId(typeId: string, existing: Set<string>): string {
  const base = typeId.split(".").pop() ?? "node";
  if (!existing.has(base)) return base;
  let i = 2;
  let id = `${base} (${i})`;
  while (existing.has(id)) {
    i += 1;
    id = `${base} (${i})`;
  }
  return id;
}

/** The Database node id; its config carries a stable, rename-proof store key. */
export const DATABASE_ID = "core.store.database";

/** The KV Store node id; its config carries a stable, rename-proof store key. */
export const KV_STORE_ID = "core.store.kv";

/** A fresh, stable store key for a data-owning node (rename-proof, persisted). */
export function freshDbKey(): string {
  return crypto.randomUUID().slice(0, 12);
}

/** The default config for a new node from its widget defaults. */
export function defaultConfig(def: NodeDef): Record<string, unknown> {
  const config: Record<string, unknown> = {};
  for (const w of def.widgets) {
    if (w.default !== undefined && w.default !== null) config[w.name] = w.default;
  }
  // A Database node OWNS its data under a stable `db_key`. Mint one at creation so
  // the store key persists with the graph and survives renaming the node (without
  // a key the backend falls back to the node id, so a rename would orphan the data).
  if (def.id === DATABASE_ID && !config.db_key) {
    config.db_key = freshDbKey();
  }
  // A KV Store node likewise OWNS its data under a stable `kv_key` (same rationale).
  if (def.id === KV_STORE_ID && !config.kv_key) {
    config.kv_key = freshDbKey();
  }
  return config;
}

/** Round a position to the design's 12px half-cell snap grid. */
export function snapPosition(pos: XYPosition, snap = 12): XYPosition {
  return {
    x: Math.round(pos.x / snap) * snap,
    y: Math.round(pos.y / snap) * snap,
  };
}

/** A fresh, unique instance id derived from a desired base (for duplicate/paste). */
export function uniqueId(base: string, existing: Set<string>): string {
  if (!existing.has(base)) return base;
  // strip a trailing -N / digits, then count up.
  const stem = base.replace(/[-_]?\d+$/, "") || base;
  let i = 2;
  let id = `${stem}-${i}`;
  while (existing.has(id)) {
    i += 1;
    id = `${stem}-${i}`;
  }
  return id;
}

/** What a copy/paste or duplicate carries: a self-contained sub-graph. */
export interface ClipboardPayload {
  nodes: Array<{ id: string; typeId: string; config: Record<string, unknown>; position: XYPosition; size?: [number, number] }>;
  edges: Array<{ source: string; sourceHandle: string; target: string; targetHandle: string; type: string }>;
}

/**
 * Clone a set of nodes (+ the edges wholly within the set) with fresh ids,
 * offset in space. Returns the new RF nodes/edges and the id remap. Used by both
 * Duplicate (in place, offset) and Paste (at the cursor).
 */
export function cloneSubgraph(
  payload: ClipboardPayload,
  existingIds: Set<string>,
  offset: XYPosition,
  defs: Map<string, NodeDef>,
): { nodes: WFNode[]; edges: WFEdge[]; idMap: Map<string, string> } {
  const idMap = new Map<string, string>();
  const taken = new Set(existingIds);
  for (const n of payload.nodes) {
    const fresh = uniqueId(n.id, taken);
    taken.add(fresh);
    idMap.set(n.id, fresh);
  }
  const nodes: WFNode[] = payload.nodes.map((n) => {
    const id = idMap.get(n.id)!;
    const config = { ...n.config };
    // A duplicated Database node starts a FRESH empty DB, so it must not inherit
    // the original's store key (that would point two nodes at the same data).
    if (n.typeId === DATABASE_ID) config.db_key = freshDbKey();
    // A duplicated KV Store node likewise starts a FRESH empty store.
    if (n.typeId === KV_STORE_ID) config.kv_key = freshDbKey();
    return {
      id,
      type: "workflow",
      position: snapPosition({ x: n.position.x + offset.x, y: n.position.y + offset.y }),
      selected: true,
      // carry the original's size so a duplicate/paste of a resizable node is the
      // same size, not a collapsed tiny copy.
      ...(n.size ? { width: n.size[0], height: n.size[1] } : {}),
      data: { instanceId: id, typeId: n.typeId, config },
    };
  });
  const edges: WFEdge[] = payload.edges
    .filter((e) => idMap.has(e.source) && idMap.has(e.target))
    .map((e) => {
      const src = idMap.get(e.source)!;
      const dst = idMap.get(e.target)!;
      return {
        id: edgeId({ src, src_port: e.sourceHandle, dst, dst_port: e.targetHandle }),
        source: src,
        sourceHandle: e.sourceHandle,
        target: dst,
        targetHandle: e.targetHandle,
        type: "typed",
        data: { type: outputType(defs, payload.nodes.find((p) => p.id === e.source)!.typeId, e.sourceHandle) },
      };
    });
  return { nodes, edges, idMap };
}
