// ============================================================================
// useGraph: the editor's single source of truth for the working graph. Wraps
// React Flow's node/edge state and exposes typed, intentful
// mutations with the editor intelligence baked in:
//   • type-checked, dynamic-port-aware connections (named growable sockets);
//   • derived connection maps the renderer / inspector read;
//   • full in-session undo / redo (Ctrl+Z / Y) over committed snapshots;
//   • duplicate, copy/paste (within & across the canvas), disable/enable;
//   • a `dirty` flag (unsaved) and a `draft` flag (running graph is behind edits);
//   • validation problems (POST /api/validate + ws `invalid`) that badge nodes.
// Everything the chrome needs to read or change about the graph goes through here.
// ============================================================================
import { useCallback, useMemo, useRef, useState } from "react";
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type EdgeChange,
  type NodeChange,
  type XYPosition,
} from "@xyflow/react";
import type { Graph, ModelManifest, NodeDef, NodeGroup, Problem } from "../types/protocol";

/** The group tint palette (keys; the design layer maps them to colours). */
const GROUP_PALETTE = ["slate", "indigo", "teal", "amber", "rose", "violet"];
import {
  cloneSubgraph,
  defaultConfig,
  DEFAULT_SIZE,
  edgeId,
  freshId,
  inputType,
  outputType,
  projectGraph,
  serializeGraph,
  snapPosition,
  uniqueId,
  type ClipboardPayload,
  type WFEdge,
  type WFNode,
} from "../lib/graphAdapter";
import { typesCompatible } from "../lib/types";
import { renameGraph } from "../lib/renameGraph";
import {
  ghostBase,
  ghostSocketName,
  isGhostHandle,
  llmPromoted,
  modelSwitchConfig,
  namesSocketsAfterSource,
  nextDynamicName,
  nodePromoted,
  parseTemplateTags,
  sourceSocketSlug,
  survivesReshape,
  TEMPLATE_ID,
} from "../lib/dynamicPorts";

export interface ConnectionRejection {
  reason: string;
  at: number;
}

/** A committed snapshot for the undo/redo stack (structure only, not selection). */
interface Snapshot {
  nodes: WFNode[];
  edges: WFEdge[];
  graphName: string;
  groups: NodeGroup[];
}

export interface ConnectionMaps {
  /** instance id -> set of connected input handle ids (literal dst_port names). */
  connectedInputs: Map<string, Set<string>>;
  /** instance id -> set of connected output handle ids. */
  connectedOutputs: Map<string, Set<string>>;
}

export interface GraphStore {
  nodes: WFNode[];
  edges: WFEdge[];
  graphName: string;
  dirty: boolean;
  draft: boolean;
  selectedId: string | null;
  selectedIds: string[];
  rejection: ConnectionRejection | null;
  problems: Problem[];
  problemsByNode: Map<string, Problem[]>;
  canUndo: boolean;
  canRedo: boolean;
  connectedInputs: Map<string, Set<string>>;
  connectedOutputs: Map<string, Set<string>>;
  disabledIds: Set<string>;
  groups: NodeGroup[];
  createGroup: (memberIds: string[]) => void;
  addToGroup: (nodeIds: string[], groupId: string) => void;
  ungroup: (nodeIds: string[]) => void;
  renameGroup: (id: string, title: string) => void;
  recolorGroup: (id: string, color: string) => void;
  groupDragStart: () => void;
  moveGroup: (id: string, dx: number, dy: number) => void;
  /** Move nodes to `positions` as ONE undo step (Tidy up), gliding there over
   *  `duration` ms (0: at once); `onDone` runs once they are there. Marks the
   *  graph unsaved, like any move. */
  arrangeNodes: (
    positions: Record<string, XYPosition>,
    opts?: { duration?: number; onDone?: () => void },
  ) => void;

  onNodesChange: (changes: NodeChange<WFNode>[]) => void;
  onEdgesChange: (changes: EdgeChange<WFEdge>[]) => void;
  onConnect: (conn: Connection) => void;
  reconnectEdge: (oldEdge: WFEdge, conn: Connection) => void;

  isValidConnection: (conn: Connection | WFEdge) => boolean;
  addNodeOfType: (typeId: string, pos: { x: number; y: number }) => string | null;
  /** The scaffold gestures available on a node: for each output port that
   *  DECLARES a scaffold and has no body of that type yet, a {port,label} the
   *  chrome renders as an "Add <node>" menu item. Generic, read from the port. */
  scaffoldsFor: (nodeId: string) => Array<{ port: string; label: string }>;
  /** Spawn the scaffold node declared on `portName` and pre-wire this port
   *  into it (target socket chosen by type-compatibility). */
  scaffoldFromPort: (nodeId: string, portName: string) => void;
  updateConfig: (id: string, key: string, value: unknown) => void;
  /** Select a model on any model-driven node: set config.model, start params from
   *  the new model's defaults, drop stale promotions, and prune only the edges
   *  whose port the reshaped node no longer has. */
  setLlmModel: (id: string, modelId: string) => void;
  /** Promote an LLM param to a typed input port (append to config.promoted). */
  promoteParam: (id: string, name: string) => void;
  /** Demote a promoted LLM param back to a widget (remove from config.promoted,
   *  pruning any wire that fed its now-removed input port). */
  unpromoteParam: (id: string, name: string) => void;
  /** Promote any node WIDGET to a typed input port (append to config.promoted). */
  promoteWidget: (id: string, name: string) => void;
  /** Demote a promoted widget back to a knob (remove from config.promoted, pruning
   *  any wire that fed its now-removed input port). */
  unpromoteWidget: (id: string, name: string) => void;
  renameNode: (id: string, name: string) => void;
  deleteNode: (id: string) => void;
  deleteSelection: () => void;
  duplicateNode: (id: string) => void;
  toggleDisabled: (id: string) => void;
  copySelection: () => void;
  cut: () => void;
  paste: (at?: XYPosition) => void;
  hasClipboard: () => boolean;
  insertOnEdge: (edgeId: string, typeId: string, at: XYPosition) => void;
  deleteEdge: (edgeId: string) => void;
  select: (id: string | null) => void;
  selectAll: () => void;
  /** Replace the canvas with `graph`. `dirty` marks it unsaved on arrival: the
   *  load healed it (dead wires dropped), so it already differs from the saved
   *  file and the next primary action must Save it before On runs that file. */
  loadGraph: (graph: Graph, opts?: { dirty?: boolean }) => void;
  setGraphName: (name: string) => void;
  toGraph: () => Graph;
  markSaved: () => void;
  clearDraft: () => void;
  undo: () => void;
  redo: () => void;
  setProblems: (problems: Problem[]) => void;
}

const PASTE_OFFSET: XYPosition = { x: 28, y: 28 };

export function useGraph(
  defs: Map<string, NodeDef>,
  models: ReadonlyMap<string, ModelManifest>,
): GraphStore {
  const [nodes, setNodes] = useState<WFNode[]>([]);
  const [edges, setEdges] = useState<WFEdge[]>([]);
  const [graphName, setGraphNameState] = useState<string>("untitled");
  const [dirty, setDirty] = useState(false);
  const [draft, setDraft] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [rejection, setRejection] = useState<ConnectionRejection | null>(null);
  const [problems, setProblemsState] = useState<Problem[]>([]);
  const [disabledIds, setDisabledIds] = useState<Set<string>>(new Set());
  const [groups, setGroups] = useState<NodeGroup[]>([]);

  // undo/redo stacks of committed snapshots; the present is held in state.
  const past = useRef<Snapshot[]>([]);
  const future = useRef<Snapshot[]>([]);
  const [histVer, setHistVer] = useState(0); // bump to recompute canUndo/canRedo
  const clipboard = useRef<ClipboardPayload | null>(null);

  const selectedId = selectedIds.length === 1 ? selectedIds[0] : null;

  // Live lookup: instance id -> its NodeDef id (the backend `type`).
  const typeById = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of nodes) m.set(n.id, n.data.typeId);
    return m;
  }, [nodes]);

  // Live lookup: instance id -> its config (the LLM type resolution reads this).
  const configById = useMemo(() => {
    const m = new Map<string, Record<string, unknown>>();
    for (const n of nodes) m.set(n.id, n.data.config);
    return m;
  }, [nodes]);

  // ── derive the connection maps once, here, so the whole app agrees ──
  const { connectedInputs, connectedOutputs } = useMemo<ConnectionMaps>(() => {
    const ins = new Map<string, Set<string>>();
    const outs = new Map<string, Set<string>>();
    for (const e of edges) {
      if (e.targetHandle) {
        if (!ins.has(e.target)) ins.set(e.target, new Set());
        ins.get(e.target)!.add(e.targetHandle);
      }
      if (e.sourceHandle) {
        if (!outs.has(e.source)) outs.set(e.source, new Set());
        outs.get(e.source)!.add(e.sourceHandle);
      }
    }
    return { connectedInputs: ins, connectedOutputs: outs };
  }, [edges]);

  const problemsByNode = useMemo(() => {
    const m = new Map<string, Problem[]>();
    for (const p of problems) {
      if (!p.node) continue;
      if (!m.has(p.node)) m.set(p.node, []);
      m.get(p.node)!.push(p);
    }
    return m;
  }, [problems]);

  // an arrange (Tidy up) still gliding: its frame loop and where it ends.
  const arranging = useRef<{ frame: number; target: Record<string, XYPosition> } | null>(null);
  /** Stop a glide where it is (undo, redo and a load put their own positions). */
  const stopArranging = () => {
    if (!arranging.current) return;
    cancelAnimationFrame(arranging.current.frame);
    arranging.current = null;
  };

  // ── history helpers ──
  // a snapshot taken mid-glide records where the nodes are going, so redo
  // after an undo lands on the finished layout, never a frame of the glide.
  const snapshot = useCallback((): Snapshot => ({
    nodes: nodes.map((n) => {
      const to = arranging.current?.target[n.id];
      return { ...n, ...(to ? { position: { ...to } } : {}), data: { ...n.data, config: { ...n.data.config } } };
    }),
    edges: edges.map((e) => ({ ...e })),
    graphName,
    groups: groups.map((g) => ({ ...g, members: [...g.members] })),
  }), [nodes, edges, graphName, groups]);

  /** Commit the CURRENT state to the undo stack before applying a mutation. */
  const commit = useCallback(() => {
    past.current.push(snapshot());
    if (past.current.length > 100) past.current.shift();
    future.current = [];
    setHistVer((v) => v + 1);
  }, [snapshot]);

  const markEdited = useCallback(() => {
    setDirty(true);
    setDraft(true); // any structural edit makes a running graph a draft
  }, []);

  // ── React Flow change handlers ──
  const draggingRef = useRef(false);
  const resizingRef = useRef(false);
  const onNodesChange = useCallback((changes: NodeChange<WFNode>[]) => {
    // snapshot once at the START of a drag/resize (so undo returns to the pre spot)
    // and on any removal; the end change marks dirty so the size/pos persists.
    const dragStart = changes.some((c) => c.type === "position" && c.dragging === true);
    const dragEnd = changes.some((c) => c.type === "position" && c.dragging === false);
    const resizing = changes.some((c) => c.type === "dimensions" && (c as { resizing?: boolean }).resizing === true);
    const resizeEnd = changes.some((c) => c.type === "dimensions" && (c as { resizing?: boolean }).resizing === false);
    const removed = changes.some((c) => c.type === "remove");
    if (removed) commit();
    else if (dragStart && !draggingRef.current) {
      draggingRef.current = true;
      commit();
    } else if (resizing && !resizingRef.current) {
      resizingRef.current = true;
      commit();
    }
    if (dragEnd) draggingRef.current = false;
    if (resizeEnd) resizingRef.current = false;

    setNodes((nds) => applyNodeChanges(changes, nds));
    if (removed) markEdited();
    if (dragEnd || resizeEnd) setDirty(true);
    const sel = changes.filter((c) => c.type === "select") as Array<{ id: string; selected: boolean }>;
    if (sel.length) {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        for (const s of sel) {
          if (s.selected) next.add(s.id);
          else next.delete(s.id);
        }
        return [...next];
      });
    }
  }, [commit, markEdited]);

  const onEdgesChange = useCallback((changes: EdgeChange<WFEdge>[]) => {
    if (changes.some((c) => c.type === "remove")) {
      commit();
      markEdited();
    }
    setEdges((eds) => applyEdgeChanges(changes, eds));
  }, [commit, markEdited]);

  // -- type-checked connection validation (used both as a guard and a gate) --
  const validate = useCallback(
    (conn: Connection | WFEdge): { ok: boolean; reason?: string } => {
      const source = conn.source;
      const target = conn.target;
      const sourceHandle = conn.sourceHandle ?? null;
      const targetHandle = conn.targetHandle ?? null;
      if (!source || !target || !sourceHandle || !targetHandle) {
        return { ok: false, reason: "incomplete connection" };
      }
      if (source === target) return { ok: false, reason: "cannot wire a node to itself" };

      const srcTypeId = typeById.get(source);
      const dstTypeId = typeById.get(target);
      const outType = srcTypeId ? outputType(defs, srcTypeId, sourceHandle, models, configById.get(source)) : "any";
      // a ghost handle resolves to its growable base's type.
      const dstHandle = isGhostHandle(targetHandle) ? ghostBase(targetHandle) : targetHandle;
      const inType = dstTypeId ? inputType(defs, dstTypeId, dstHandle, models, configById.get(target)) : "any";
      if (!typesCompatible(outType, inType)) {
        return { ok: false, reason: `${outType} ✗ ${inType}` };
      }
      return { ok: true };
    },
    [defs, typeById, configById, models],
  );

  const isValidConnection = useCallback(
    (conn: Connection | WFEdge) => validate(conn).ok,
    [validate],
  );

  const applyConnection = useCallback(
    (conn: Connection, removeEdgeId?: string) => {
      const verdict = validate(conn);
      if (!verdict.ok) {
        setRejection({ reason: verdict.reason ?? "incompatible", at: Date.now() });
        return;
      }
      const source = conn.source!;
      const target = conn.target!;
      const sourceHandle = conn.sourceHandle!;
      let targetHandle = conn.targetHandle!;
      const dstTypeId = typeById.get(target);

      // dropping on a growable ghost mints a fresh named socket. A port that
      // declares ghost_base (Template tag, HTTP/KV/DB tag, Build field) names the
      // socket after the WIRED SOURCE, slugified (lowercased, spaces -> _, symbols
      // stripped) so "Chat Append (User)" becomes chat_append_user and is a valid
      // {tag} token. Generic growable bases (Compute value0, LLM tool0) auto-number.
      if (isGhostHandle(targetHandle)) {
        const base = ghostBase(targetHandle);
        const dstDef = defs.get(dstTypeId ?? "");
        if (namesSocketsAfterSource(dstDef, base)) {
          // de-dup against the tags already on this node (template tags + wired
          // sockets), so two wires from sources that slug to the same name differ.
          const used = new Set<string>(
            edges.filter((e) => e.target === target).map((e) => e.targetHandle ?? ""),
          );
          if (dstTypeId === TEMPLATE_ID) {
            const tplNode = nodes.find((n) => n.id === target);
            for (const t of parseTemplateTags(String(tplNode?.data.config.template ?? ""))) used.add(t);
          }
          // Wireless In names each socket {source}.{port} (e.g. response.out,
          // response.trigger) so two ports from one source stay distinct and
          // self-describing; other ghost bases (Template/HTTP tags) name after the
          // source node only (the {tag} grammar has no dot). Numbered past the used
          // names AND the node's declared ports, so a source called "trigger" never
          // mints a socket shadowing `trigger`. Renaming the source re-mints the
          // socket by this same rule (lib/renameGraph).
          targetHandle = ghostSocketName(dstDef, sourceSocketSlug(dstDef, source, sourceHandle), used);
        } else {
          const used = new Set(
            edges.filter((e) => e.target === target).map((e) => e.targetHandle ?? ""),
          );
          targetHandle = nextDynamicName(base, used);
        }
      }

      const srcTypeId = typeById.get(source);
      const srcType = srcTypeId ? outputType(defs, srcTypeId, sourceHandle, models, configById.get(source)) : "any";
      commit();
      setEdges((eds) => {
        // when reconnecting, drop the edge whose endpoint was dragged first.
        const base = removeEdgeId ? eds.filter((e) => e.id !== removeEdgeId) : eds;
        // a latching input holds at most one wire; replace any existing wire on
        // the same (target, targetHandle). Trigger/growable inputs may stack.
        const pruned = base.filter(
          (e) => !(e.target === target && e.targetHandle === targetHandle),
        );
        return addEdge(
          {
            source,
            target,
            sourceHandle,
            targetHandle,
            id: edgeId({ src: source, src_port: sourceHandle, dst: target, dst_port: targetHandle }),
            type: "typed",
            reconnectable: "target",
            data: { type: srcType },
          },
          pruned,
        );
      });
      markEdited();
    },
    [defs, typeById, configById, models, validate, edges, commit, markEdited],
  );

  const onConnect = useCallback((conn: Connection) => applyConnection(conn), [applyConnection]);

  // Drag an existing edge's endpoint onto another handle to re-route it (React
  // Flow onReconnect). We re-route the DESTINATION end; the edge is reconnectable
  // "target" only, so grabbing the source end is a no-op (start a fresh wire from
  // the output handle instead). Validates like a normal connect.
  const reconnectEdge = useCallback(
    (oldEdge: WFEdge, conn: Connection) => applyConnection(conn, oldEdge.id),
    [applyConnection],
  );

  const addNodeOfType = useCallback(
    (typeId: string, pos: { x: number; y: number }) => {
      const def = defs.get(typeId);
      if (!def) return null;
      commit();
      let newId = "";
      setNodes((nds) => {
        const existing = new Set(nds.map((n) => n.id));
        newId = freshId(typeId, existing);
        const node: WFNode = {
          id: newId,
          type: "workflow",
          position: snapPosition(pos),
          selected: true,
          data: { instanceId: newId, typeId, config: defaultConfig(def) },
        };
        // mint a default size for resizable types so a dragged-in node has
        // sensible width/height from the start (otherwise React Flow defaults
        // to a tiny box and the body looks cropped). Same map toRFNode uses.
        const sz = DEFAULT_SIZE[typeId];
        if (sz) {
          node.width = sz[0];
          node.height = sz[1];
        }
        return [...nds.map((n) => ({ ...n, selected: false })), node];
      });
      setSelectedIds([newId]);
      markEdited();
      return newId;
    },
    [defs, commit, markEdited],
  );

  // ── scaffold: build a pre-wired cluster generically ──
  // It reads a declaration (a Port's `scaffold`) rather than branching on a
  // node id, and mints nodes+edges in a SINGLE commit so one undo reverts the
  // whole gesture. It mirrors addNodeOfType/insertOnEdge: newId is
  // derived from the current `nodes` set (not captured inside a setState updater).

  const scaffoldsFor = useCallback((nodeId: string): Array<{ port: string; label: string }> => {
    const node = nodes.find((n) => n.id === nodeId);
    const def = node && defs.get(node.data.typeId);
    if (!node || !def) return [];
    const out: Array<{ port: string; label: string }> = [];
    for (const p of def.outputs) {
      if (!p.scaffold) continue;
      const sd = defs.get(p.scaffold);
      if (!sd) continue;
      // offer the gesture only while this port has no body of the scaffold type
      // yet (the port may still feed OTHER consumers, e.g. Tool.call -> LLM.tools).
      const hasBody = edges.some(
        (e) => e.source === nodeId && e.sourceHandle === p.name &&
          nodes.find((n) => n.id === e.target)?.data.typeId === p.scaffold,
      );
      if (hasBody) continue;
      out.push({ port: p.name, label: `Add ${sd.name}` });
    }
    return out;
  }, [nodes, edges, defs]);

  const scaffoldFromPort = useCallback((nodeId: string, portName: string) => {
    const node = nodes.find((n) => n.id === nodeId);
    const def = node && defs.get(node.data.typeId);
    const port = def?.outputs.find((p) => p.name === portName);
    const scaffoldType = port?.scaffold ?? undefined;
    const scaffoldDef = scaffoldType ? defs.get(scaffoldType) : undefined;
    if (!node || !def || !scaffoldType || !scaffoldDef) return;
    const srcType = outputType(defs, node.data.typeId, portName, models, node.data.config);
    // wire into the first input on the spawned node that accepts this type.
    const target =
      scaffoldDef.inputs.find((p) => typesCompatible(srcType, p.type)) ?? scaffoldDef.inputs[0];
    if (!target) return;
    commit();
    const existing = new Set(nodes.map((n) => n.id));
    const newId = freshId(scaffoldType, existing);
    const pos = snapPosition({ x: node.position.x + (node.width ?? 240) + 60, y: node.position.y });
    const nn: WFNode = {
      id: newId,
      type: "workflow",
      position: pos,
      selected: true,
      data: { instanceId: newId, typeId: scaffoldType, config: defaultConfig(scaffoldDef) },
    };
    const sz = DEFAULT_SIZE[scaffoldType];
    if (sz) { nn.width = sz[0]; nn.height = sz[1]; }
    setNodes((nds) => [...nds.map((n) => ({ ...n, selected: false })), nn]);
    setEdges((eds) =>
      addEdge(
        {
          source: nodeId,
          target: newId,
          sourceHandle: portName,
          targetHandle: target.name,
          id: edgeId({ src: nodeId, src_port: portName, dst: newId, dst_port: target.name }),
          type: "typed",
          reconnectable: "target",
          data: { type: srcType },
        },
        eds,
      ),
    );
    setSelectedIds([newId]);
    markEdited();
  }, [nodes, defs, models, commit, markEdited]);

  const updateConfig = useCallback((id: string, key: string, value: unknown) => {
    commit();
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: value } } } : n,
      ),
    );
    markEdited();
  }, [commit, markEdited]);

  // ── model selection + param promotion (the capability-driven nodes) ──

  /** Select a model on any model-driven node (LLM / TTS / STT / Embed / Rerank):
   *  the config starts from the new manifest's defaults (modelSwitchConfig, so no
   *  param of the previous model leaks into this one), and a wire is kept only
   *  while its port still exists on the reshaped node and its type still fits
   *  (survivesReshape, read from the same concreteInputs/concreteOutputs the
   *  canvas renders). So a TTS keeps its text/lang wires across a switch, and an
   *  LLM drops a wire on an input or output the new model no longer has. */
  const setLlmModel = useCallback((id: string, modelId: string) => {
    const node = nodes.find((n) => n.id === id);
    const def = node ? defs.get(node.data.typeId) : undefined;
    if (!node || !def) return;
    const nextConfig = modelSwitchConfig(def, node.data.config, modelId, models.get(modelId));
    const connected = connectedInputs.get(id) ?? new Set<string>();
    commit();
    setNodes((nds) =>
      nds.map((n) => (n.id === id ? { ...n, data: { ...n.data, config: nextConfig } } : n)),
    );
    setEdges((eds) =>
      eds.filter((e) => {
        if (e.source === id) {
          return survivesReshape(def, nextConfig, connected,
            { side: "out", handle: e.sourceHandle ?? "" }, models, typesCompatible);
        }
        if (e.target !== id) return true;
        const srcTypeId = typeById.get(e.source);
        const carried = srcTypeId
          ? outputType(defs, srcTypeId, e.sourceHandle ?? "", models, configById.get(e.source))
          : "any";
        return survivesReshape(def, nextConfig, connected,
          { side: "in", handle: e.targetHandle ?? "", type: carried }, models, typesCompatible);
      }),
    );
    markEdited();
  }, [nodes, defs, models, connectedInputs, typeById, configById, commit, markEdited]);

  const promoteParam = useCallback((id: string, name: string) => {
    const node = nodes.find((n) => n.id === id);
    if (!node || node.data.typeId !== "core.ai.llm") return;
    const current = llmPromoted(node.data.config);
    if (current.includes(name)) return;
    commit();
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, config: { ...n.data.config, promoted: [...current, name] } } }
          : n,
      ),
    );
    markEdited();
  }, [nodes, commit, markEdited]);

  const unpromoteParam = useCallback((id: string, name: string) => {
    const node = nodes.find((n) => n.id === id);
    if (!node || node.data.typeId !== "core.ai.llm") return;
    const current = llmPromoted(node.data.config);
    if (!current.includes(name)) return;
    commit();
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, config: { ...n.data.config, promoted: current.filter((p) => p !== name) } } }
          : n,
      ),
    );
    // the input port for this param disappears; drop any wire feeding it.
    setEdges((eds) => eds.filter((e) => !(e.target === id && e.targetHandle === name)));
    markEdited();
  }, [nodes, commit, markEdited]);

  // ── universal knob promotion: the same config.promoted machinery, for ANY node ──

  /** Promote any node widget to a typed input port: append its name to
   *  config.promoted (de-duped), if the name is a real widget on the def. */
  const promoteWidget = useCallback((id: string, name: string) => {
    const node = nodes.find((n) => n.id === id);
    if (!node) return;
    const def = defs.get(node.data.typeId);
    const widget = def?.widgets.find((w) => w.name === name);
    // secret widgets must NOT be promoted: that would leak the value onto a
    // plaintext wire (shown live + logged). Keep them as masked knobs.
    if (!def || !widget || widget.kind === "secret") return;
    const current = nodePromoted(node.data.config);
    if (current.includes(name)) return;
    commit();
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, config: { ...n.data.config, promoted: [...current, name] } } }
          : n,
      ),
    );
    markEdited();
  }, [nodes, defs, commit, markEdited]);

  /** Demote a promoted widget back to a knob: remove from config.promoted and drop
   *  any wire feeding the input port that is about to disappear. */
  const unpromoteWidget = useCallback((id: string, name: string) => {
    const node = nodes.find((n) => n.id === id);
    if (!node) return;
    const current = nodePromoted(node.data.config);
    if (!current.includes(name)) return;
    commit();
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, config: { ...n.data.config, promoted: current.filter((p) => p !== name) } } }
          : n,
      ),
    );
    setEdges((eds) => eds.filter((e) => !(e.target === id && e.targetHandle === name)));
    markEdited();
  }, [nodes, commit, markEdited]);

  /** Rename a node. The graph rewrite is pure (lib/renameGraph): its own wires
   *  re-point, the sockets a ghost drop named after it follow with their {tag}
   *  tokens (only in the node that owns each socket), and its group keeps it.
   *  A declared port that shares the old name, such as TTS `text`, never moves.
   *  A blank name, the same id or another node's id changes nothing. */
  const renameNode = useCallback(
    (id: string, name: string) => {
      const renamed = renameGraph({ nodes, edges, groups }, defs, id, name);
      if (!renamed) return;
      const clean = renamed.id;
      commit();
      setNodes(renamed.nodes);
      setEdges(renamed.edges);
      setGroups(renamed.groups);
      setSelectedIds((cur) => cur.map((s) => (s === id ? clean : s)));
      setDisabledIds((prev) => {
        if (!prev.has(id)) return prev;
        const next = new Set(prev);
        next.delete(id);
        next.add(clean);
        return next;
      });
      markEdited();
    },
    [nodes, edges, groups, defs, commit, markEdited],
  );

  const removeIds = useCallback((ids: Set<string>) => {
    if (ids.size === 0) return;
    commit();
    setNodes((nds) => nds.filter((n) => !ids.has(n.id)));
    setEdges((eds) => eds.filter((e) => !ids.has(e.source) && !ids.has(e.target)));
    setSelectedIds((cur) => cur.filter((s) => !ids.has(s)));
    setDisabledIds((prev) => {
      if (![...ids].some((id) => prev.has(id))) return prev;
      const next = new Set(prev);
      for (const id of ids) next.delete(id);
      return next;
    });
    markEdited();
  }, [commit, markEdited]);

  const deleteNode = useCallback((id: string) => removeIds(new Set([id])), [removeIds]);

  const deleteSelection = useCallback(() => {
    if (selectedIds.length) removeIds(new Set(selectedIds));
  }, [selectedIds, removeIds]);

  const deleteEdge = useCallback((id: string) => {
    commit();
    setEdges((eds) => eds.filter((e) => e.id !== id));
    markEdited();
  }, [commit, markEdited]);

  // ── duplicate / copy / paste ──
  const payloadFor = useCallback((ids: string[]): ClipboardPayload => {
    const set = new Set(ids);
    return {
      nodes: nodes
        .filter((n) => set.has(n.id))
        .map((n) => ({
          id: n.id, typeId: n.data.typeId, config: { ...n.data.config }, position: { ...n.position },
          ...(typeof n.width === "number" && typeof n.height === "number"
            ? { size: [n.width, n.height] as [number, number] }
            : {}),
        })),
      edges: edges
        .filter((e) => set.has(e.source) && set.has(e.target))
        .map((e) => ({
          source: e.source,
          sourceHandle: e.sourceHandle ?? "",
          target: e.target,
          targetHandle: e.targetHandle ?? "",
          type: e.data?.type ?? "any",
        })),
    };
  }, [nodes, edges]);

  const duplicateNode = useCallback((id: string) => {
    const ids = selectedIds.includes(id) && selectedIds.length > 1 ? selectedIds : [id];
    const payload = payloadFor(ids);
    if (payload.nodes.length === 0) return;
    const existing = new Set(nodes.map((n) => n.id));
    // clone ONCE so node ids, edge endpoints and the new selection all agree.
    const { nodes: clones, edges: cloneEdges, idMap } = cloneSubgraph(payload, existing, PASTE_OFFSET, defs);
    commit();
    setNodes((nds) => [...nds.map((n) => ({ ...n, selected: false })), ...clones]);
    setEdges((eds) => [...eds, ...cloneEdges]);
    setSelectedIds([...idMap.values()]);
    markEdited();
  }, [selectedIds, payloadFor, nodes, defs, commit, markEdited]);

  const copySelection = useCallback(() => {
    if (selectedIds.length === 0) return;
    clipboard.current = payloadFor(selectedIds);
  }, [selectedIds, payloadFor]);

  const cut = useCallback(() => {
    if (selectedIds.length === 0) return;
    clipboard.current = payloadFor(selectedIds);
    removeIds(new Set(selectedIds));
  }, [selectedIds, payloadFor, removeIds]);

  const paste = useCallback((at?: XYPosition) => {
    const payload = clipboard.current;
    if (!payload || payload.nodes.length === 0) return;
    // offset so the paste lands near the cursor (or nudged from the original).
    let offset = PASTE_OFFSET;
    if (at) {
      const minX = Math.min(...payload.nodes.map((n) => n.position.x));
      const minY = Math.min(...payload.nodes.map((n) => n.position.y));
      offset = { x: at.x - minX, y: at.y - minY };
    }
    const existing = new Set(nodes.map((n) => n.id));
    const { nodes: clones, edges: cloneEdges, idMap } = cloneSubgraph(payload, existing, offset, defs);
    commit();
    setNodes((nds) => [...nds.map((n) => ({ ...n, selected: false })), ...clones]);
    setEdges((eds) => [...eds, ...cloneEdges]);
    setSelectedIds([...idMap.values()]);
    markEdited();
  }, [nodes, defs, commit, markEdited]);

  const hasClipboard = useCallback(() => !!clipboard.current && clipboard.current.nodes.length > 0, []);

  const toggleDisabled = useCallback((id: string) => {
    setDisabledIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    markEdited();
  }, [markEdited]);

  // ── visual groups (editor-only; the runtime never sees them) ──
  // Box a set of nodes; the rect is derived from members' bbox at render, so it
  // auto-resizes. A node belongs to at most one group.
  const createGroup = useCallback((memberIds: string[]) => {
    const members = memberIds.filter((id) => nodes.some((n) => n.id === id));
    if (members.length === 0) return;
    commit();
    setGroups((prev) => {
      const cleaned = prev
        .map((g) => ({ ...g, members: g.members.filter((m) => !members.includes(m)) }))
        .filter((g) => g.members.length > 0);
      const used = new Set(cleaned.map((g) => g.color));
      const color = GROUP_PALETTE.find((c) => !used.has(c)) ?? GROUP_PALETTE[cleaned.length % GROUP_PALETTE.length];
      const taken = new Set(prev.map((g) => g.id));
      let i = 1; let id = "group-1";
      while (taken.has(id)) id = `group-${++i}`;
      return [...cleaned, { id, title: "Group", color, members }];
    });
    markEdited();
  }, [nodes, commit, markEdited]);

  /**
   * Drag-in: add node(s) to an existing group. A node belongs to at
   * most one group, so the ids are first stripped from any OTHER group. No-op
   * (no undo snapshot, no dirty) when every id is already a member of `groupId`.
   */
  const addToGroup = useCallback((nodeIds: string[], groupId: string) => {
    const ids = nodeIds.filter((id) => nodes.some((n) => n.id === id));
    if (ids.length === 0) return;
    const target = groups.find((g) => g.id === groupId);
    if (!target || ids.every((id) => target.members.includes(id))) return;
    commit();
    setGroups((prev) => prev
      .map((g) => (g.id === groupId
        ? { ...g, members: [...g.members, ...ids.filter((id) => !g.members.includes(id))] }
        : { ...g, members: g.members.filter((m) => !ids.includes(m)) }))
      .filter((g) => g.members.length > 0));
    markEdited();
  }, [nodes, groups, commit, markEdited]);

  /** Remove node(s) from their group(s); a group with no members left is dropped. */
  const ungroup = useCallback((nodeIds: string[]) => {
    const rm = new Set(nodeIds);
    commit();
    setGroups((prev) => prev
      .map((g) => ({ ...g, members: g.members.filter((m) => !rm.has(m)) }))
      .filter((g) => g.members.length > 0));
    markEdited();
  }, [commit, markEdited]);

  const renameGroup = useCallback((id: string, title: string) => {
    commit();
    setGroups((prev) => prev.map((g) => (g.id === id ? { ...g, title } : g)));
    markEdited();
  }, [commit, markEdited]);

  const recolorGroup = useCallback((id: string, color: string) => {
    commit();
    setGroups((prev) => prev.map((g) => (g.id === id ? { ...g, color } : g)));
    markEdited();
  }, [commit, markEdited]);

  /** snapshot once at the start of a group drag (so undo returns to the pre spot). */
  const groupDragStart = useCallback(() => commit(), [commit]);
  /** translate every member of a group by a flow-space delta (during a drag). */
  const moveGroup = useCallback((id: string, dx: number, dy: number) => {
    const g = groups.find((x) => x.id === id);
    if (!g) return;
    const m = new Set(g.members);
    setNodes((prev) => prev.map((n) => (m.has(n.id) ? { ...n, position: { x: n.position.x + dx, y: n.position.y + dy } } : n)));
    markEdited();
  }, [groups, markEdited]);

  const arrangeNodes = useCallback((
    positions: Record<string, XYPosition>,
    opts?: { duration?: number; onDone?: () => void },
  ) => {
    // a glide still running ends where it was going first
    const running = arranging.current;
    stopArranging();
    const at = (n: WFNode) => running?.target[n.id] ?? n.position;
    const from = new Map<string, XYPosition>();
    for (const n of nodes) {
      const to = positions[n.id];
      const p = at(n);
      if (to && (to.x !== p.x || to.y !== p.y)) from.set(n.id, { ...p });
    }
    if (from.size === 0) {
      if (running) setNodes((prev) => prev.map((n) => (running.target[n.id] ? { ...n, position: { ...running.target[n.id] } } : n)));
      opts?.onDone?.();
      return;
    }
    commit();
    setDirty(true);
    const target: Record<string, XYPosition> = { ...(running?.target ?? {}), ...positions };
    const place = (k: number) => setNodes((prev) => prev.map((n) => {
      const to = target[n.id];
      if (!to) return n;
      const f = from.get(n.id);
      if (!f || k >= 1) return n.position.x === to.x && n.position.y === to.y ? n : { ...n, position: { ...to } };
      return { ...n, position: { x: f.x + (to.x - f.x) * k, y: f.y + (to.y - f.y) * k } };
    }));
    const duration = Math.max(0, opts?.duration ?? 0);
    if (duration === 0) {
      place(1);
      opts?.onDone?.();
      return;
    }
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      place(1 - Math.pow(1 - t, 3)); // ease out
      if (t < 1 && arranging.current) {
        arranging.current.frame = requestAnimationFrame(step);
      } else {
        arranging.current = null;
        opts?.onDone?.();
      }
    };
    arranging.current = { frame: requestAnimationFrame(step), target };
  }, [nodes, commit]);

  /** Splice a node onto an existing edge: src -> [new] -> dst (best-effort ports). */
  const insertOnEdge = useCallback((eid: string, typeId: string, at: XYPosition) => {
    const def = defs.get(typeId);
    const edge = edges.find((e) => e.id === eid);
    if (!def || !edge) return;
    commit();
    const existing = new Set(nodes.map((n) => n.id));
    const newId = freshId(typeId, existing);
    // choose a sensible in/out port on the inserted node by type-compatibility.
    const wireType = edge.data?.type ?? "any";
    const inPort = def.inputs.find((p) => typesCompatible(wireType, p.type)) ?? def.inputs[0];
    const outPort = def.outputs.find((p) => typesCompatible(p.type, wireType)) ?? def.outputs[0];

    const node: WFNode = {
      id: newId,
      type: "workflow",
      position: snapPosition(at),
      selected: true,
      data: { instanceId: newId, typeId, config: defaultConfig(def) },
    };
    setNodes((nds) => [...nds.map((n) => ({ ...n, selected: false })), node]);
    setEdges((eds) => {
      const rest = eds.filter((e) => e.id !== eid);
      const next = [...rest];
      if (inPort) {
        next.push({
          id: edgeId({ src: edge.source, src_port: edge.sourceHandle ?? "", dst: newId, dst_port: inPort.name }),
          source: edge.source,
          sourceHandle: edge.sourceHandle,
          target: newId,
          targetHandle: inPort.name,
          type: "typed",
          data: { type: wireType },
        });
      }
      if (outPort) {
        next.push({
          id: edgeId({ src: newId, src_port: outPort.name, dst: edge.target, dst_port: edge.targetHandle ?? "" }),
          source: newId,
          sourceHandle: outPort.name,
          target: edge.target,
          targetHandle: edge.targetHandle,
          type: "typed",
          data: { type: outPort.type },
        });
      }
      return next;
    });
    setSelectedIds([newId]);
    markEdited();
  }, [defs, edges, nodes, commit, markEdited]);

  const select = useCallback((id: string | null) => {
    setSelectedIds(id ? [id] : []);
    setNodes((nds) => nds.map((n) => ({ ...n, selected: n.id === id })));
  }, []);

  const selectAll = useCallback(() => {
    setNodes((nds) => nds.map((n) => ({ ...n, selected: true })));
    setSelectedIds(nodes.map((n) => n.id));
  }, [nodes]);

  const loadGraph = useCallback(
    (graph: Graph, opts?: { dirty?: boolean }) => {
      stopArranging();
      const { nodes: rfNodes, edges: rfEdges } = projectGraph(graph, defs, models);
      setNodes(rfNodes);
      setEdges(rfEdges);
      setGraphNameState(graph.name ?? "untitled");
      setSelectedIds([]);
      setDirty(!!opts?.dirty);
      setDraft(false);
      setProblemsState([]);
      // restore the bypassed set from the persisted `disabled` flags.
      setDisabledIds(new Set((graph.nodes ?? []).filter((n) => n.disabled).map((n) => n.id)));
      // restore visual groups, dropping any member id no longer in the graph.
      {
        const ids = new Set((graph.nodes ?? []).map((n) => n.id));
        setGroups((graph.groups ?? [])
          .map((g) => ({ ...g, members: g.members.filter((m) => ids.has(m)) }))
          .filter((g) => g.members.length > 0));
      }
      past.current = [];
      future.current = [];
      setHistVer((v) => v + 1);
    },
    [defs, models],
  );

  const setGraphName = useCallback((name: string) => {
    commit();
    setGraphNameState(name);
    markEdited();
  }, [commit, markEdited]);

  const toGraph = useCallback(
    () => serializeGraph(graphName, nodes, edges, disabledIds, groups),
    [graphName, nodes, edges, disabledIds, groups],
  );

  const markSaved = useCallback(() => setDirty(false), []);
  const clearDraft = useCallback(() => setDraft(false), []);
  const setProblems = useCallback((p: Problem[]) => setProblemsState(p), []);

  const restore = useCallback((snap: Snapshot) => {
    stopArranging();
    setNodes(snap.nodes.map((n) => ({ ...n, data: { ...n.data, config: { ...n.data.config } } })));
    setEdges(snap.edges.map((e) => ({ ...e })));
    setGraphNameState(snap.graphName);
    setGroups(snap.groups.map((g) => ({ ...g, members: [...g.members] })));
    setDirty(true);
    setDraft(true);
  }, []);

  const undo = useCallback(() => {
    const prev = past.current.pop();
    if (!prev) return;
    future.current.push(snapshot());
    restore(prev);
    setHistVer((v) => v + 1);
  }, [snapshot, restore]);

  const redo = useCallback(() => {
    const nxt = future.current.pop();
    if (!nxt) return;
    past.current.push(snapshot());
    restore(nxt);
    setHistVer((v) => v + 1);
  }, [snapshot, restore]);

  void histVer; // referenced to recompute canUndo/canRedo on bump
  const canUndo = past.current.length > 0;
  const canRedo = future.current.length > 0;

  return {
    nodes,
    edges,
    graphName,
    dirty,
    draft,
    selectedId,
    selectedIds,
    rejection,
    problems,
    problemsByNode,
    canUndo,
    canRedo,
    connectedInputs,
    connectedOutputs,
    disabledIds,
    groups,
    createGroup,
    addToGroup,
    ungroup,
    renameGroup,
    recolorGroup,
    groupDragStart,
    moveGroup,
    arrangeNodes,
    onNodesChange,
    onEdgesChange,
    onConnect,
    reconnectEdge,
    isValidConnection,
    addNodeOfType,
    scaffoldsFor,
    scaffoldFromPort,
    updateConfig,
    setLlmModel,
    promoteParam,
    unpromoteParam,
    promoteWidget,
    unpromoteWidget,
    renameNode,
    deleteNode,
    deleteSelection,
    duplicateNode,
    toggleDisabled,
    copySelection,
    cut,
    paste,
    hasClipboard,
    insertOnEdge,
    deleteEdge,
    select,
    selectAll,
    loadGraph,
    setGraphName,
    toGraph,
    markSaved,
    clearDraft,
    undo,
    redo,
    setProblems,
  };
}
