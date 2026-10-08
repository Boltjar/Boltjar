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
export const GRAPH_FORMAT = 2;

/** The model value format 1 had for "run whatever model can run now". It is
 *  gone: a model can cost money, so only a person picks one. */
const FORMAT_1_AUTO = "auto";

/**
 * A graph the editor holds from before GRAPH_FORMAT (a localStorage draft never
 * passes through the server) in the current format, the way the server's
 * migrate (boltjar/graph_format.py) turns it: format 2 drops the "auto" model,
 * so a model picker that held it holds none. `changed` says whether anything
 * was dropped. A current graph comes back as is; the input is never mutated.
 */
export function migrateGraph(graph: Graph): { graph: Graph; changed: boolean } {
  if ((graph.format ?? 0) >= GRAPH_FORMAT) return { graph, changed: false };
  let changed = false;
  const nodes = graph.nodes.map((n) => {
    if (n.config?.model !== FORMAT_1_AUTO) return n;
    changed = true;
    const config = { ...n.config };
    delete config.model;
    return { ...n, config };
  });
  return { graph: { ...graph, format: GRAPH_FORMAT, nodes }, changed };
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
