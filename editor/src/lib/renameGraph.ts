// ============================================================================
// renameGraph: what renaming a node does to the rest of the working graph.
//
// A node id is referenced by its own wires (source / target), by the dynamic
// sockets a ghost drop named after it (a Template, HTTP, KV or DB tag, a Build
// field, a Queue or Wireless In socket) with the `{tag}` tokens that read them,
// and by the group that boxes it. A rename re-points exactly those. Nothing that
// merely shares the old name moves: a declared port such as TTS `text`, a
// promoted knob's port, an author-named tag or an auto-numbered socket keeps its
// name, and a `{text}` token is rewritten only inside the node whose socket
// followed. Pure: returns new arrays and never mutates its input.
// ============================================================================
import type { NodeDef, NodeGroup } from "../types/protocol";
import {
  ghostSocketName,
  namesSocketsAfterSource,
  nodePromoted,
  reservedInputNames,
  sourceSocketSlug,
  TEMPLATE_ID,
  templateTags,
  WIRELESS_IN_ID,
  WIRELESS_OUT_ID,
} from "./dynamicPorts";
import { edgeId, type WFEdge, type WFNode } from "./graphAdapter";

/** The part of the working graph a rename rewrites (useGraph's state). */
export interface RenameState {
  nodes: readonly WFNode[];
  edges: readonly WFEdge[];
  groups: readonly NodeGroup[];
}

/** The graph after the rename; `id` is the node's new (trimmed) id. */
export interface Renamed {
  id: string;
  nodes: WFNode[];
  edges: WFEdge[];
  groups: NodeGroup[];
}

/**
 * Rename node `from` to `to` across the graph. Returns null when there is
 * nothing to apply: an unknown node, a blank name, the same id, or a name that
 * another node already has (ids stay unique, and moving this node's wires onto
 * that node would silently rewire it).
 */
export function renameGraph(
  graph: RenameState,
  defs: ReadonlyMap<string, NodeDef>,
  from: string,
  to: string,
): Renamed | null {
  const id = to.trim();
  if (!id || id === from) return null;
  if (!graph.nodes.some((n) => n.id === from) || graph.nodes.some((n) => n.id === id)) return null;

  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const moved = socketRenames(graph, defs, byId, from, id);
  const outPorts = wirelessOutRenames(graph.nodes, byId, moved);

  const nodes = graph.nodes.map((n) => {
    if (n.id === from) return { ...n, id, data: { ...n.data, instanceId: id } };
    const renames = moved.get(n.id);
    const config = renames ? retoken(n.data.config, renames) : n.data.config;
    return config === n.data.config ? n : { ...n, data: { ...n.data, config } };
  });

  const edges = graph.edges.map((e) => {
    const source = e.source === from ? id : e.source;
    const target = e.target === from ? id : e.target;
    const targetHandle = e.source === from
      ? moved.get(e.target)?.get(e.targetHandle ?? "") ?? e.targetHandle
      : e.targetHandle;
    const src = byId.get(e.source);
    const sourceHandle = src?.data.typeId === WIRELESS_OUT_ID
      ? outPorts.get(channelOf(src))?.get(e.sourceHandle ?? "") ?? e.sourceHandle
      : e.sourceHandle;
    if (source === e.source && target === e.target &&
        targetHandle === e.targetHandle && sourceHandle === e.sourceHandle) return e;
    return {
      ...e, source, target, sourceHandle, targetHandle,
      id: edgeId({ src: source, src_port: sourceHandle ?? "", dst: target, dst_port: targetHandle ?? "" }),
    };
  });

  const groups = graph.groups.map((g) => (g.members.includes(from)
    ? { ...g, members: g.members.map((m) => (m === from ? id : m)) }
    : g));

  return { id, nodes, edges, groups };
}

/**
 * The sockets that follow the rename, per target node (old name -> new name).
 * A socket follows only when a wire FROM the renamed node lands on it and it is
 * the very name a ghost drop of that wire minted from the old id. Each is
 * re-minted as a fresh drop from the new id would be: numbered past the node's
 * other sockets, its declared ports and (on a Template) every tag its text holds.
 */
function socketRenames(
  graph: RenameState,
  defs: ReadonlyMap<string, NodeDef>,
  byId: ReadonlyMap<string, WFNode>,
  from: string,
  to: string,
): Map<string, Map<string, string>> {
  // the following sockets per target, in wire order (a Wireless In can take two
  // ports of one source, answer.out and answer.trigger, and both follow).
  const follows = new Map<string, Map<string, string>>(); // target -> old -> source port
  for (const e of graph.edges) {
    if (e.source !== from || e.target === from) continue;
    const target = byId.get(e.target);
    const def = target && defs.get(target.data.typeId);
    const handle = e.targetHandle ?? "";
    const port = e.sourceHandle ?? "";
    if (!target || !def || !namedAfter(def, target.data.config, handle, from, port)) continue;
    if (!follows.has(e.target)) follows.set(e.target, new Map());
    follows.get(e.target)!.set(handle, port);
  }

  const moved = new Map<string, Map<string, string>>();
  for (const [targetId, sockets] of follows) {
    const target = byId.get(targetId)!;
    const def = defs.get(target.data.typeId)!;
    const used = new Set(graph.edges.filter((e) => e.target === targetId).map((e) => e.targetHandle ?? ""));
    if (def.id === TEMPLATE_ID) {
      for (const tag of templateTags(def, String(target.data.config.template ?? ""))) used.add(tag);
    }
    for (const old of sockets.keys()) used.delete(old); // vacated by the rename
    const renames = new Map<string, string>();
    for (const [old, port] of sockets) {
      const name = ghostSocketName(def, sourceSocketSlug(def, to, port), used);
      used.add(name);
      if (name !== old) renames.set(old, name);
    }
    if (renames.size) moved.set(targetId, renames);
  }
  return moved;
}

/** Whether `handle` on a node of `def` is a dynamic socket a ghost drop named
 *  after `source`.`sourcePort`: never a declared port (TTS or Vectors `text`),
 *  never a promoted knob's port (HTTP `url`), never a socket on an auto-numbered
 *  base, and exactly the name that source asks for (so an author-named tag the
 *  source merely feeds, such as `{greeting}`, keeps its name). */
function namedAfter(
  def: NodeDef,
  config: Record<string, unknown>,
  handle: string,
  source: string,
  sourcePort: string,
): boolean {
  if (!def.inputs.some((p) => p.growable && namesSocketsAfterSource(def, p.name))) return false;
  if (reservedInputNames(def).has(handle)) return false;
  if (nodePromoted(config).includes(handle) && def.widgets.some((w) => w.name === handle)) return false;
  return handle === sourceSocketSlug(def, source, sourcePort);
}

/** A Wireless Out mirrors the sockets of its channel's Wireless In and its wires
 *  leave from those socket NAMES, so when an In socket follows the rename, the
 *  Out wires on that channel follow with it (channel -> old -> new). The In
 *  that owns a channel is the first one on it, as the runtime and the canvas
 *  both resolve it; a second In on the channel is mirrored by no Out. */
function wirelessOutRenames(
  nodes: readonly WFNode[],
  byId: ReadonlyMap<string, WFNode>,
  moved: ReadonlyMap<string, ReadonlyMap<string, string>>,
): Map<string, ReadonlyMap<string, string>> {
  const out = new Map<string, ReadonlyMap<string, string>>();
  for (const [targetId, renames] of moved) {
    const target = byId.get(targetId)!;
    if (target.data.typeId !== WIRELESS_IN_ID) continue;
    const ch = channelOf(target);
    const owner = nodes.find((n) => n.data.typeId === WIRELESS_IN_ID && channelOf(n) === ch);
    if (owner?.id === targetId) out.set(ch, renames);
  }
  return out;
}

function channelOf(n: WFNode): string {
  return String(n.data.config.channel ?? "1");
}

/** Rewrite each `{old}` token to `{new}` in the node's text knobs (the Template
 *  text, an HTTP url/body, a DB sql...), all names in one pass so a rename can
 *  never be rewritten twice. Only the exact token moves: `{texts}` stays. */
function retoken(
  config: Record<string, unknown>,
  renames: ReadonlyMap<string, string>,
): Record<string, unknown> {
  const escaped = [...renames.keys()].map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const token = new RegExp(`\\{(${escaped.join("|")})\\}`, "g");
  let out: Record<string, unknown> | null = null;
  for (const [key, value] of Object.entries(config)) {
    if (typeof value !== "string") continue;
    const next = value.replace(token, (_, name: string) => `{${renames.get(name)}}`);
    if (next === value) continue;
    out = out ?? { ...config };
    out[key] = next;
  }
  return out ?? config;
}
