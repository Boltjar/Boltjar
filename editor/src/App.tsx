// ============================================================================
// App: composes the BOLTJAR mission-control editor.
// Loads the node catalog and the chat console graph, owns the power model
// (On / Off / Restart with validate-before-on and a draft state), derives the
// connection + inbound-source maps the node renderer reads via context, wires
// the run socket, the context menus, the command palette and keyboard, and lays
// out the four-edge chrome around the canvas.
// ============================================================================
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import type { Graph } from "./types/protocol";
import { useObjectInfo } from "./hooks/useObjectInfo";
import { useModels } from "./hooks/useModels";
import { useRunSocket } from "./hooks/useRunSocket";
import { useTabsStatus } from "./hooks/useTabsStatus";
import { useGraph } from "./hooks/useGraph";
import { useVersion } from "./hooks/useVersion";
import { EditorProvider, type InboundWire } from "./lib/editorContext";
import { outputType, DATABASE_ID, KV_STORE_ID, GRAPH_FORMAT } from "./lib/graphAdapter";
import { WIRELESS_IN_ID, WIRELESS_OUT_ID, ROUTER_ID, isGhostHandle, type WirelessChannelMap, type WirelessSocket } from "./lib/dynamicPorts";
import { deadWireNotice, healDeadWires } from "./lib/deadWires";
import { notifyStoreChanged } from "./lib/storeEvents";
import { fetchServerGraph, serverError, unreadableNotice } from "./lib/serverGraph";
import { mod } from "./lib/platform";
import { DOCS_URL, FEEDBACK_URL, SPONSOR_URL, bugReportUrl, copyText, diagnosticsText, openExternal, osName } from "./lib/help";
import logoUrl from "./assets/boltjar-logo-dark.svg";
import { PRESETS } from "./lib/presets";
import { CommandBar, type PowerPhase, type PrimaryAction } from "./components/CommandBar";
import { NodeLibrary } from "./components/NodeLibrary";
import { SavedWorkflowsPanel } from "./components/SavedWorkflowsPanel";
import { Canvas, type CanvasMenuRequest } from "./components/canvas/Canvas";
import { GROUP_COLORS } from "./components/canvas/GroupsLayer";
import { NodeModal } from "./components/canvas/NodeModal";
import { CommandPalette, type PaletteAction } from "./components/CommandPalette";
import { ContextMenu, type MenuItem } from "./components/ContextMenu";
import { ProblemsPanel } from "./components/ProblemsPanel";
import { StatusBar } from "./components/StatusBar";
import { ConnectionsWindow } from "./components/ConnectionsWindow";
import { type WorkflowTab } from "./components/WorkflowTabs";
import { isPermutation, bringToFront } from "./components/WorkflowTabs.test.helper";

const EMPTY_GRAPH: Graph = { name: "untitled", nodes: [], edges: [] };
/** The chat console loaded on startup. */
const BOOT_GRAPH = "chat";
/** Per-slug working-draft autosave key. The `:v2` suffix is the draft format
 *  version: bumping it abandons drafts saved in an older shape (e.g. one whose
 *  edges name a firing port that was since renamed, which passes validation but
 *  never fires the graph), so the editor reloads the fresh server graph. */
function draftKey(slug: string): string {
  return `boltjar:draft:${slug}:v2`;
}
/** Persists open/closed state and library mode for both rails. */
const RAILS_KEY = "boltjar:ui:rails";
/** Persists the open tabs (browser-like multi-workflow strip) + active slug. */
const TABS_KEY = "boltjar:ui:tabs";

interface TabsState {
  open: string[];
  active: string | null;
}
const DEFAULT_TABS: TabsState = { open: [BOOT_GRAPH], active: BOOT_GRAPH };

function readTabs(): TabsState {
  try {
    const raw = localStorage.getItem(TABS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<TabsState>;
      const open = Array.isArray(parsed.open) ? parsed.open.filter((s) => typeof s === "string" && s.length > 0) : [];
      const active = typeof parsed.active === "string" && open.includes(parsed.active) ? parsed.active : (open[0] ?? null);
      return { open, active };
    }
  } catch { /* ignore */ }
  return { ...DEFAULT_TABS };
}

function writeTabs(s: TabsState) {
  try { localStorage.setItem(TABS_KEY, JSON.stringify(s)); } catch { /* ignore */ }
}

type RailsState = {
  library: "open" | "closed";
};
const DEFAULT_RAILS: RailsState = { library: "open" };

function readRails(): RailsState {
  try {
    const raw = localStorage.getItem(RAILS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<RailsState>;
      return {
        library: parsed.library === "closed" ? "closed" : "open",
      };
    }
  } catch { /* ignore */ }
  return { ...DEFAULT_RAILS };
}

function writeRails(s: RailsState) {
  try { localStorage.setItem(RAILS_KEY, JSON.stringify(s)); } catch { /* ignore */ }
}

type MenuState = CanvasMenuRequest;

export default function App() {
  const { nodes: defList, defs, loading: defsLoading, error: defsError } = useObjectInfo();
  const { models, loading: modelsLoading, meta: modelsMeta, reload: reloadModels, refresh: refreshModels } = useModels();

  // ── tabs (browser-like multi-workflow strip) ─────────────────────────────
  // Each open tab is a backend slug. The EDITOR shows the active tab's graph;
  // the per-tab status dot is driven by useTabsStatus (one cheap ws per slug).
  const [tabsState, setTabsStateRaw] = useState<TabsState>(readTabs);
  const setTabsState = useCallback((updater: (prev: TabsState) => TabsState) => {
    setTabsStateRaw((prev) => {
      const next = updater(prev);
      writeTabs(next);
      return next;
    });
  }, []);
  const activeSlug = tabsState.active;
  const openSlugs = tabsState.open;
  // Take a tab off the strip. When it was the active one, the previous tab (or
  // the next, or none when the strip goes empty) becomes active.
  const dropTab = useCallback((slug: string) => {
    setTabsState((prev) => {
      const idx = prev.open.indexOf(slug);
      if (idx < 0) return prev;
      const open = prev.open.filter((s) => s !== slug);
      let active = prev.active;
      if (active === slug) {
        active = open[idx - 1] ?? open[idx] ?? open[0] ?? null;
      }
      return { open, active };
    });
  }, [setTabsState]);

  // The runtime socket follows the active tab. The hook tears down + reopens
  // the ws when the slug changes, and the Hub replays its status on subscribe.
  const socket = useRunSocket(activeSlug ?? "_default");
  // One lightweight per-slug ws to drive the green dot on every tab.
  const tabsStatus = useTabsStatus(openSlugs);

  const graph = useGraph(defs, models);
  const version = useVersion();

  const [graphLoading, setGraphLoading] = useState(true);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [connectionsOpen, setConnectionsOpen] = useState(false);
  // the Help menu hangs from the top bar's Help button (its bottom-right corner).
  const [helpAnchor, setHelpAnchor] = useState<{ x: number; y: number } | null>(null);
  const [aboutOpen, setAboutOpen] = useState(false);

  // ── rail open/closed + library mode (persisted to localStorage) ──
  const [rails, setRailsRaw] = useState<RailsState>(readRails);
  const setRails = useCallback((updater: (prev: RailsState) => RailsState) => {
    setRailsRaw((prev) => {
      const next = updater(prev);
      writeRails(next);
      return next;
    });
  }, []);
  const [saving, setSaving] = useState(false);
  const [lastSaved, setLastSaved] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [cursor, setCursor] = useState({ x: 0, y: 0 });
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [problemsOpen, setProblemsOpen] = useState(false);
  // a rename request the matching node card picks up to enter inline edit mode.
  const [renameTarget, setRenameTarget] = useState<{ id: string; nonce: number } | null>(null);
  // same idea for a group (the group menu's "Rename" raises it).
  const [renameGroupTarget, setRenameGroupTarget] = useState<{ id: string; nonce: number } | null>(null);
  // a live drag-from-port (its source type) so compatible targets highlight.
  const [connecting, setConnecting] = useState<{ fromType: string; fromId: string } | null>(null);
  const lastFlowPos = useRef({ x: 280, y: 200 });

  const {
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
    onNodesChange,
    onEdgesChange,
    onConnect,
    reconnectEdge,
    isValidConnection,
    addNodeOfType,
    addPreset,
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
    toGraph,
    markSaved,
    clearDraft,
    undo,
    redo,
    setProblems,
  } = graph;

  // Track which slug is currently rendered by useGraph so we can save THAT
  // slug's draft on tab switch (and not stomp the new slug's draft with it).
  const loadedSlugRef = useRef<string | null>(null);

  // ── slug-switch effect: when the active tab changes, save the outgoing
  //    slug's draft into localStorage, then load the new slug's draft (or
  //    fetch it from the server; a slug the server does not know starts as an
  //    empty graph). Runs on first mount with the boot slug
  //    (defaultTabs.active) and on every switch. ──
  useEffect(() => {
    // wait for BOTH catalogs: every load heals the graph's dead wires, and that
    // judges each node against its definition and (model-driven nodes: LLM, TTS,
    // STT...) its model manifest. Judged earlier, an LLM would read as having no
    // image / audio / tools port and lose those wires.
    if (defsLoading || modelsLoading) return;
    if (!activeSlug) {
      // no active tab: empty editor, no load. (we render a CTA in the canvas.)
      loadedSlugRef.current = null;
      loadGraph({ ...EMPTY_GRAPH });
      setGraphLoading(false);
      return;
    }
    if (loadedSlugRef.current === activeSlug) return; // already showing it

    // Save the outgoing tab's current draft (if any) BEFORE swapping in the new
    // graph. `loadedSlugRef.current` is the slug whose state is in `toGraph()`.
    const outgoing = loadedSlugRef.current;
    if (outgoing && outgoing !== activeSlug) {
      try {
        localStorage.setItem(draftKey(outgoing), JSON.stringify(toGraph()));
      } catch { /* storage full / unavailable: drop */ }
    }

    setGraphLoading(true);
    const target = activeSlug;
    // Every graph loads healed (lib/deadWires): a wire on a port its node no
    // longer has is dropped before it reaches the canvas, each removal is
    // reported on the console, and the draft autosave below persists the clean
    // graph. A node whose definition or model manifest is unknown is not judged.
    const loadHealed = (g: Graph) => {
      const { graph: clean, removed } = healDeadWires(g, defs, models);
      // a healed graph differs from the saved file, so it arrives unsaved: the
      // primary button offers Save, and On never runs the file's dead wire.
      loadGraph(clean, { dirty: removed.length > 0 });
      for (const w of removed) socket.notice(deadWireNotice(w), "warn");
    };
    (async () => {
      // A workflow's identity is its slug; the graph `name` is always forced to
      // the slug on load so the internal name can never diverge from it.
      try {
        const saved = localStorage.getItem(draftKey(target));
        if (saved) {
          const g = JSON.parse(saved) as Graph;
          if (g && Array.isArray(g.nodes) && g.nodes.length > 0) {
            loadHealed({ ...g, name: target });
            loadedSlugRef.current = target;
            setGraphLoading(false);
            return;
          }
        }
      } catch { /* corrupt draft, fall through */ }
      // else fetch the server graph. Only a slug the server does not know opens
      // empty; one it cannot serve (a graph from a newer Boltjar, a server error)
      // is reported and its tab closed, and the canvas keeps what it showed, so
      // no Save can put an empty graph in that file's place.
      const loaded = await fetchServerGraph(target);
      if (loaded.kind === "unreadable") {
        socket.notice(unreadableNotice(target, loaded.error), "bad");
        setGraphLoading(false);
        dropTab(target);
        return;
      }
      if (loaded.kind === "graph") loadHealed({ ...loaded.graph, name: target });
      else loadGraph({ ...EMPTY_GRAPH, name: target });
      loadedSlugRef.current = target;
      setGraphLoading(false);
    })();
  // intentionally narrow deps: re-run only when slug or catalog readiness flips.
  // toGraph + loadGraph are stable across renders of the same data.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSlug, defsLoading, modelsLoading]);

  // ── auto-persist the working draft (debounced) so a refresh restores it.
  //    Keyed on the slug currently rendered so we never write the outgoing
  //    tab's edits under the incoming slug. ──
  useEffect(() => {
    if (graphLoading) return;
    const target = loadedSlugRef.current;
    if (!target) return;
    const t = window.setTimeout(() => {
      try {
        localStorage.setItem(draftKey(target), JSON.stringify(toGraph()));
      } catch {
        /* storage unavailable / full: ignore */
      }
    }, 250);
    return () => window.clearTimeout(t);
  }, [toGraph, graphLoading]);


  // node id -> typeId / config, used across the derivations below.
  const typeById = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of nodes) m.set(n.id, n.data.typeId);
    return m;
  }, [nodes]);
  const configById = useMemo(() => {
    const m = new Map<string, Record<string, unknown>>();
    for (const n of nodes) m.set(n.id, n.data.config);
    return m;
  }, [nodes]);

  // raw inbound type for each (node:port) from edges: the source's declared
  // output type, no wireless/bypass resolution (one level), used as the base.
  const rawInType = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of edges) {
      m.set(`${e.target}:${e.targetHandle ?? ""}`,
        outputType(defs, typeById.get(e.source) ?? "", e.sourceHandle ?? "", models, configById.get(e.source)));
    }
    return m;
  }, [edges, defs, models, typeById, configById]);

  // the raw source feeding each (node:port), used to chase a Router to its origin.
  const rawInSource = useMemo(() => {
    const m = new Map<string, { src: string; srcPort: string }>();
    for (const e of edges) m.set(`${e.target}:${e.targetHandle ?? ""}`, { src: e.source, srcPort: e.sourceHandle ?? "" });
    return m;
  }, [edges]);

  // type of a source port, following a bypass passthrough ONE level (Preview/Chat:
  // `out` follows the wire into `in`) but NOT wireless: the base for the channel
  // socket type, so a wire carried through a Preview keeps its real type.
  const sourceTypeNoWireless = useMemo(() => (srcId: string, srcPort: string): string => {
    const def = defs.get(typeById.get(srcId) ?? "");
    const bypass = def?.bypass;
    if (bypass) {
      const inP = Object.keys(bypass).find((i) => bypass[i] === srcPort);
      if (inP) return rawInType.get(`${srcId}:${inP}`) ?? outputType(defs, typeById.get(srcId) ?? "", srcPort, models, configById.get(srcId));
    }
    return outputType(defs, typeById.get(srcId) ?? "", srcPort, models, configById.get(srcId));
  }, [defs, models, typeById, configById, rawInType]);

  // per-channel sockets each Wireless In broadcasts: name + wire type + the REAL
  // source (the runtime flattens wireless, emitting on the source not the wireless
  // port), so the Out mirrors the ports and displays/edges read the real stream.
  // First In per channel wins. Computed from edges (independent of inboundSources).
  const wirelessChannels = useMemo<WirelessChannelMap>(() => {
    const owner = new Map<string, string>(); // channel -> owning In id
    const chById = new Map<string, string>(); // In id -> channel
    for (const n of nodes) {
      if (n.data.typeId !== WIRELESS_IN_ID) continue;
      const ch = String(n.data.config.channel ?? "1");
      chById.set(n.id, ch);
      if (!owner.has(ch)) owner.set(ch, n.id);
    }
    const m = new Map<string, WirelessSocket[]>();
    for (const e of edges) {
      const ch = chById.get(e.target);
      if (!ch || owner.get(ch) !== e.target) continue; // only the owning In
      const socket = e.targetHandle ?? "";
      if (isGhostHandle(socket)) continue;
      if (!m.has(ch)) m.set(ch, []);
      m.get(ch)!.push({ name: socket, type: sourceTypeNoWireless(e.source, e.sourceHandle ?? ""), src: e.source, srcPort: e.sourceHandle ?? "" });
    }
    return m;
  }, [nodes, edges, sourceTypeNoWireless]);

  // The effective output of a source port: chases FLATTENED reroutes to where the
  // value really comes from. A Router (and a Wireless Out) is flattened by the
  // runtime, so its output resolves to the REAL upstream source (recursively, so a
  // chain of routers / a router behind a wireless still reports the origin's
  // name+type). A live passthrough (Preview/Chat: out follows in) keeps the node as
  // `src` (it really emits) but its TYPE follows the wire into `in`. null otherwise.
  const effectiveOutput = useMemo(() => {
    const resolve = (srcId: string, srcPort: string, depth: number): { type: string; src: string; srcPort: string } | null => {
      if (depth > 32) return null;
      const t = typeById.get(srcId);
      if (t === ROUTER_ID) {
        const up = rawInSource.get(`${srcId}:in`);
        if (!up) return null;
        return resolve(up.src, up.srcPort, depth + 1)
          ?? { type: rawInType.get(`${srcId}:in`) ?? "any", src: up.src, srcPort: up.srcPort };
      }
      if (t === WIRELESS_OUT_ID) {
        const ch = String(configById.get(srcId)?.channel ?? "1");
        const s = wirelessChannels.get(ch)?.find((x) => x.name === srcPort);
        if (!s) return null;
        return resolve(s.src, s.srcPort, depth + 1) ?? { type: s.type, src: s.src, srcPort: s.srcPort };
      }
      const bypass = defs.get(t ?? "")?.bypass;
      if (bypass) {
        const inP = Object.keys(bypass).find((i) => bypass[i] === srcPort);
        if (inP) {
          const ty = rawInType.get(`${srcId}:${inP}`);
          if (ty) return { type: ty, src: srcId, srcPort };
        }
      }
      return null;
    };
    return (srcId: string, srcPort: string) => resolve(srcId, srcPort, 0);
  }, [typeById, configById, defs, wirelessChannels, rawInType, rawInSource]);

  // ── derive inbound-source map (with resolved source types) for Previews ──
  // a wire from a Wireless Out resolves to its real upstream source; a wire from a
  // passthrough's `out` keeps the node as source but carries the real `in` type.
  const inboundSources = useMemo(() => {
    const m = new Map<string, InboundWire[]>();
    for (const e of edges) {
      const eff = effectiveOutput(e.source, e.sourceHandle ?? "");
      const src = eff ? eff.src : e.source;
      const srcPort = eff ? eff.srcPort : (e.sourceHandle ?? "");
      const srcType = eff ? eff.type : outputType(defs, typeById.get(e.source) ?? "", e.sourceHandle ?? "", models, configById.get(e.source));
      // `live` tests the RAW drawn edge (e.id === `src:src_port->dst:dst_port`)
      // against the server's live set: a wire added since the last Save & Restart
      // is absent, so its Preview/Chat/pulse stays a draft ("Save & Restart to tap").
      const wire: InboundWire = { src, srcPort, srcType, dstPort: e.targetHandle ?? "", live: socket.liveEdges.has(e.id) };
      if (!m.has(e.target)) m.set(e.target, []);
      m.get(e.target)!.push(wire);
    }
    return m;
  }, [edges, defs, models, typeById, configById, effectiveOutput, socket.liveEdges]);

  // channel -> owning Wireless In (first wins): a channel may have only one In,
  // so another In's channel dropdown excludes the taken ones.
  const wirelessInOwners = useMemo<ReadonlyMap<string, string>>(() => {
    const m = new Map<string, string>();
    for (const n of nodes) {
      if (n.data.typeId !== WIRELESS_IN_ID) continue;
      const ch = String(n.data.config.channel ?? "1");
      if (!m.has(ch)) m.set(ch, n.id);
    }
    return m;
  }, [nodes]);

  // ── the editor power phase + adaptive primary action ──
  const phase: PowerPhase = useMemo(() => {
    if (problems.length > 0) return "invalid";
    if (socket.power === "on") return draft ? "draft" : "on";
    return "off";
  }, [problems.length, socket.power, draft]);

  const primary: PrimaryAction = useMemo(() => {
    if (socket.power === "on") return draft ? "save-restart" : "none";
    // off:
    if (dirty) return "save";
    if (nodes.length > 0) return "on";
    return "none";
  }, [socket.power, draft, dirty, nodes.length]);

  // ── persistence ──
  const putGraph = useCallback(async (): Promise<boolean> => {
    const target = activeSlug;
    if (!target) return false; // no open tab
    setSaving(true);
    try {
      const g = toGraph();
      // save under the active tab's slug so explicit Save round-trips on reload.
      const res = await fetch(`/api/graphs/${target}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(g),
      });
      if (res.ok) {
        try {
          localStorage.setItem(draftKey(target), JSON.stringify(g));
        } catch {
          /* ignore */
        }
        markSaved();
        // explicit PUT is a clean checkpoint: the active draft now matches what
        // the server holds, so the draft flag should reset too (otherwise the
        // primary stays "Save & Restart" after Save -> On).
        clearDraft();
        setLastSaved(Date.now());
        return true;
      }
      // a refused save says why (a saved graph this Boltjar cannot read is kept).
      socket.notice(`did not save ${target}: ${await serverError(res)}`, "bad");
      return false;
    } catch {
      return false;
    } finally {
      setSaving(false);
    }
  }, [toGraph, markSaved, activeSlug, socket.notice]);

  // ── power: validate then start (gated), stop, restart ──
  const powerOn = useCallback(async () => {
    const g = toGraph();
    try {
      const res = await fetch("/api/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(g),
      });
      if (res.ok) {
        const { problems: probs } = (await res.json()) as { problems: typeof problems };
        if (probs.length > 0) {
          setProblems(probs);
          setProblemsOpen(true);
          return; // refuse to turn On
        }
      }
    } catch {
      /* if validate is unreachable, fall through and let the ws gate it */
    }
    setProblems([]);
    socket.on(g);
  }, [toGraph, socket, setProblems]);

  const powerOff = useCallback(() => {
    socket.off();
    clearDraft();
  }, [socket, clearDraft]);

  const restart = useCallback(async () => {
    const ok = await putGraph();
    if (ok) {
      socket.restart(toGraph());
      clearDraft();
    }
  }, [putGraph, socket, toGraph, clearDraft]);

  // ── the adaptive primary button ──
  const onPrimary = useCallback(async () => {
    if (primary === "save") {
      await putGraph();
    } else if (primary === "on") {
      await powerOn();
    } else if (primary === "save-restart") {
      await restart();
    }
  }, [primary, putGraph, powerOn, restart]);

  const handleAddAtCenter = useCallback(
    (typeId: string) => {
      // when the palette was opened by a drop-in-empty (drop position pinned),
      // mint the new node EXACTLY there so it lands under the user's cursor.
      const pin = dropPinRef.current;
      const p = pin ?? lastFlowPos.current;
      dropPinRef.current = null;
      addNodeOfType(typeId, pin ?? { x: p.x + Math.random() * 40, y: p.y + Math.random() * 40 });
    },
    [addNodeOfType],
  );

  // Drop a picker preset (a pre-wired cluster) at the same spot a single node
  // would land. Presets are data (lib/presets.ts); this just routes to addPreset.
  const handleAddPreset = useCallback(
    (presetId: string) => {
      const pin = dropPinRef.current;
      const p = pin ?? lastFlowPos.current;
      dropPinRef.current = null;
      addPreset(presetId, pin ?? { x: p.x + Math.random() * 40, y: p.y + Math.random() * 40 });
    },
    [addPreset],
  );

  // when a wire is dropped on empty space, remember the drop position and the
  // type filter; the palette opens already filtered, and the picked node lands
  // exactly at the drop spot.
  const dropPinRef = useRef<{ x: number; y: number } | null>(null);
  const [paletteTypeFilter, setPaletteTypeFilter] = useState<{ type: string; direction: "input" | "output" } | null>(null);
  const onDropInEmpty = useCallback((info: { type: string; direction: "input" | "output"; pos: { x: number; y: number } }) => {
    dropPinRef.current = info.pos;
    setPaletteTypeFilter({ type: info.type, direction: info.direction });
    setPaletteOpen(true);
  }, []);

  const liveCount = useMemo(
    () => Object.values(socket.nodeStatus).filter((s) => s === "running").length,
    [socket.nodeStatus],
  );

  // ── context menus ──
  const onContextRequest = useCallback((req: CanvasMenuRequest) => {
    lastFlowPos.current = req.flow;
    // a right-click on a node selects it first (so menu acts on the right node).
    if (req.kind === "node" && req.targetId && !selectedIds.includes(req.targetId)) {
      select(req.targetId);
    }
    setMenu(req);
    setPaletteOpen(false);
  }, [select, selectedIds]);

  // Deleting a Database / KV Store node ERASES its data file on disk (privacy:
  // sensitive data must not linger; there is no undo), so it goes through a
  // confirm. Every other node deletes with no friction. `run` is the graph
  // deletion to perform on confirm; `stores` carry the kind + key to erase.
  const [pendingDelete, setPendingDelete] = useState<
    { run: () => void; stores: Array<{ id: string; kind: "db" | "kv"; key: string }> } | null
  >(null);
  const requestDelete = useCallback((ids: string[], run: () => void) => {
    const stores = ids
      .map((id) => nodes.find((n) => n.id === id))
      .filter((n) => n && (n.data.typeId === DATABASE_ID || n.data.typeId === KV_STORE_ID))
      .map((n) => {
        const isDb = n!.data.typeId === DATABASE_ID;
        const cfg = n!.data.config ?? {};
        const key = String((isDb ? cfg.db_key : cfg.kv_key) ?? n!.id);
        return { id: n!.id, kind: (isDb ? "db" : "kv") as "db" | "kv", key };
      });
    if (stores.length) setPendingDelete({ run, stores });
    else run();
  }, [nodes]);

  // After a store node's data file is erased on delete, we keep a schema snapshot
  // (columns only, no rows) keyed by node id. If undo brings the node back, this
  // effect recreates its EMPTY tables: columns return, data does not.
  const erasedSchemasRef = useRef<Map<string, { key: string; tables: unknown[] }>>(new Map());
  useEffect(() => {
    if (erasedSchemasRef.current.size === 0) return;
    for (const n of nodes) {
      const snap = erasedSchemasRef.current.get(n.id);
      if (!snap) continue;
      erasedSchemasRef.current.delete(n.id);
      void fetch(`/api/store/db/${encodeURIComponent(snap.key)}/restore`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tables: snap.tables }),
      })
        // the tables are back: the table pickers wired to this store read them again
        .then((r) => { if (r.ok) notifyStoreChanged("db", snap.key); })
        .catch(() => { /* best effort: the node is back, the empty tables will lazily recreate on next edit */ });
    }
  }, [nodes]);

  const menuItems: MenuItem[] = useMemo(() => {
    if (!menu) return [];
    if (menu.kind === "node" && menu.targetId) {
      const id = menu.targetId;
      const disabled = disabledIds.has(id);
      const multi = selectedIds.length > 1 && selectedIds.includes(id);
      // scaffold gestures declared on the node's output ports (e.g. a Tool's
      // `call` offers "Add Tool Args"). Generic: read from scaffoldsFor, shown
      // first so the body-builder is the top affordance on a fresh node.
      const scaffolds = multi ? [] : scaffoldsFor(id);
      return [
        ...scaffolds.map((s) => ({
          id: `scaffold-${s.port}`,
          label: s.label,
          icon: "add-circle-outline",
          run: () => scaffoldFromPort(id, s.port),
        })),
        { id: "rename", label: "Rename", icon: "pencil", separatorBefore: scaffolds.length > 0, run: () => beginRename(id) },
        { id: "duplicate", label: multi ? `Duplicate ${selectedIds.length}` : "Duplicate", icon: "duplicate-outline", kbd: mod("D"), run: () => duplicateNode(id) },
        { id: "copy", label: "Copy", icon: "copy-outline", kbd: mod("C"), run: () => copySelection() },
        { id: "disable", label: disabled ? "Enable" : "Disable", icon: disabled ? "checkmark-circle" : "ban-outline", run: () => toggleDisabled(id) },
        // Group vs Ungroup, never both: a node already in a group only offers Ungroup.
        ...((groups.some((g) => (multi ? selectedIds : [id]).some((x) => g.members.includes(x))))
          ? [{ id: "ungroup", label: "Ungroup", icon: "remove-circle-outline", separatorBefore: true, run: () => ungroup(multi ? selectedIds : [id]) }]
          : [{ id: "group", label: multi ? `Group ${selectedIds.length}` : "Group", icon: "albums-outline", separatorBefore: true, run: () => createGroup(multi ? selectedIds : [id]) }]),
        { id: "delete", label: multi ? `Delete ${selectedIds.length}` : "Delete", icon: "trash-outline", kbd: "Del", danger: true, separatorBefore: true, run: () => (multi ? requestDelete(selectedIds, deleteSelection) : requestDelete([id], () => deleteNode(id))) },
      ];
    }
    if (menu.kind === "group" && menu.targetId) {
      const g = groups.find((x) => x.id === menu.targetId);
      if (!g) return [];
      return [
        { id: "grp-rename", label: "Rename", icon: "pencil", run: () => beginGroupRename(g.id) },
        ...Object.entries(GROUP_COLORS).map(([key, hex], i) => ({
          id: `grp-color-${key}`,
          label: key.charAt(0).toUpperCase() + key.slice(1),
          swatch: hex,
          active: g.color === key,
          separatorBefore: i === 0,
          run: () => recolorGroup(g.id, key),
        })),
        { id: "grp-ungroup", label: "Ungroup", icon: "remove-circle-outline", separatorBefore: true, run: () => ungroup(g.members) },
      ];
    }
    if (menu.kind === "edge" && menu.targetId) {
      const id = menu.targetId;
      return [
        { id: "del-edge", label: "Delete wire", icon: "trash-outline", danger: true, run: () => deleteEdge(id) },
      ];
    }
    // canvas
    return [
      { id: "paste", label: "Paste", icon: "clipboard-outline", kbd: mod("V"), disabled: !hasClipboard(), run: () => paste(menu.flow) },
      { id: "selectall", label: "Select all", icon: "scan-outline", kbd: mod("A"), separatorBefore: true, run: () => selectAll() },
      { id: "fit", label: "Fit view", icon: "scan-outline", run: () => fitRef.current?.() },
    ];
    // beginGroupRename is a stable [] useCallback declared below; omit it from
    // deps (matches beginRename) to avoid a use-before-declaration reference.
  }, [menu, disabledIds, selectedIds, groups, createGroup, ungroup, recolorGroup, duplicateNode, copySelection, toggleDisabled, deleteSelection, deleteNode, deleteEdge, requestDelete, hasClipboard, paste, selectAll, scaffoldsFor, scaffoldFromPort]);

  // ── tab handlers (open / activate / close) ──
  const activateTab = useCallback((slug: string) => {
    setTabsState((prev) => (prev.active === slug ? prev : { ...prev, active: slug }));
  }, [setTabsState]);

  // Reorder the open list (drag-and-drop in WorkflowTabs). Refuses anything
  // that is not a permutation of the current open set; active is unchanged.
  const reorderTabs = useCallback((nextOpen: string[]) => {
    setTabsState((prev) => {
      if (!isPermutation(prev.open, nextOpen)) return prev;
      return { ...prev, open: nextOpen };
    });
  }, [setTabsState]);

  // Activate from the More dropdown: bring the slug to position 0 of `open`
  // (so it always lands inside the visible 4) AND set active.
  const activateTabFromOverflow = useCallback((slug: string) => {
    setTabsState((prev) => {
      if (!prev.open.includes(slug)) return prev;
      const open = bringToFront(prev.open, slug);
      return { open, active: slug };
    });
  }, [setTabsState]);

  const openTab = useCallback((slug: string) => {
    setTabsState((prev) => {
      if (prev.open.includes(slug)) {
        return prev.active === slug ? prev : { ...prev, active: slug };
      }
      return { open: [...prev.open, slug], active: slug };
    });
  }, [setTabsState]);

  // Create a brand-new untitled workflow as a fresh tab. We pick a stable slug
  // (untitled, untitled-2, untitled-3...) by scanning local drafts AND the open
  // tab list so the user never lands on a slug already in use elsewhere.
  const newWorkflow = useCallback(() => {
    setTabsState((prev) => {
      const taken = new Set<string>(prev.open);
      try {
        for (let i = 0; i < localStorage.length; i += 1) {
          const k = localStorage.key(i);
          if (k && k.startsWith("boltjar:draft:") && k.endsWith(":v2")) {
            taken.add(k.slice("boltjar:draft:".length, -":v2".length));
          }
        }
      } catch { /* ignore */ }
      let slug = "untitled";
      let n = 2;
      while (taken.has(slug)) {
        slug = `untitled-${n}`;
        n += 1;
      }
      // seed an empty draft so the slug-switch effect loads a fresh canvas
      // instead of fetching /api/graphs/<slug> (which will 404 for a new one).
      try {
        localStorage.setItem(
          `boltjar:draft:${slug}:v2`,
          JSON.stringify({ format: GRAPH_FORMAT, name: slug, nodes: [], edges: [] }),
        );
      } catch { /* ignore */ }
      return { open: [...prev.open, slug], active: slug };
    });
    // ensure the rail is open so the user sees their new tab + workflow list.
    setRails((p) => p.library === "open" ? p : { ...p, library: "open" });
  }, [setTabsState, setRails]);

  const closeTab = useCallback((slug: string) => {
    // 1) send Off over the tab's ws (safe no-op when already off).
    //    The cheapest way is a one-shot fetch-style ws: open, send, close.
    //    Sending over the active socket only works when this IS the active tab,
    //    so use a dedicated short-lived ws keyed to the closing slug.
    try {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${window.location.host}/ws?slug=${encodeURIComponent(slug)}`);
      ws.onopen = () => {
        try { ws.send(JSON.stringify({ action: "off" })); } catch { /* ignore */ }
        // give the server a tick to process before tearing the connection down.
        window.setTimeout(() => { try { ws.close(); } catch { /* ignore */ } }, 60);
      };
      ws.onerror = () => { try { ws.close(); } catch { /* ignore */ } };
    } catch { /* socket unavailable: backend probably already gone */ }

    // 2) drop the slug's draft (it's no longer represented by an open tab).
    try { localStorage.removeItem(draftKey(slug)); } catch { /* ignore */ }

    // 3) take it off the strip (the neighbouring tab becomes active).
    dropTab(slug);
  }, [dropTab]);

  // ── tab context menu actions ────────────────────────────────────────────
  // Rename: move the draft under a new slug and PUT/DELETE on the server so the
  // saved workflow list keeps in step. The active selection follows.
  const renameTab = useCallback(async (slug: string, nextSlug: string) => {
    const fresh = nextSlug.trim();
    if (!fresh || fresh === slug) return;
    // refuse clashes with an already-open or already-saved slug: only a slug the
    // server does not know is free (one it cannot read is still a saved graph).
    if ((await fetchServerGraph(fresh)).kind !== "missing") return;
    setTabsState((prev) => {
      if (prev.open.includes(fresh)) return prev;
      // copy draft to the new key, drop the old
      try {
        const raw = localStorage.getItem(draftKey(slug));
        if (raw) {
          const g = JSON.parse(raw);
          g.name = fresh;
          localStorage.setItem(draftKey(fresh), JSON.stringify(g));
        }
        localStorage.removeItem(draftKey(slug));
      } catch { /* ignore */ }
      const open = prev.open.map((s) => s === slug ? fresh : s);
      const active = prev.active === slug ? fresh : prev.active;
      return { open, active };
    });
    // best-effort: PUT under the new slug + DELETE the old one
    try {
      const raw = localStorage.getItem(draftKey(fresh));
      if (raw) {
        await fetch(`/api/graphs/${encodeURIComponent(fresh)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: raw,
        });
      }
      await fetch(`/api/graphs/${encodeURIComponent(slug)}`, { method: "DELETE" }).catch(() => {});
    } catch { /* ignore */ }
  }, [setTabsState]);

  // Clone: fetch the source (draft preferred, else server), mint untitled / -2 /
  // ..., seed a fresh draft, open as a new active tab.
  const cloneTab = useCallback(async (slug: string) => {
    let raw = localStorage.getItem(draftKey(slug));
    if (!raw) {
      try {
        const r = await fetch(`/api/graphs/${encodeURIComponent(slug)}`);
        if (r.ok) raw = await r.text();
      } catch { /* ignore */ }
    }
    if (!raw) return;
    setTabsState((prev) => {
      const taken = new Set<string>(prev.open);
      try {
        for (let i = 0; i < localStorage.length; i += 1) {
          const k = localStorage.key(i);
          if (k && k.startsWith("boltjar:draft:") && k.endsWith(":v2")) {
            taken.add(k.slice("boltjar:draft:".length, -":v2".length));
          }
        }
      } catch { /* ignore */ }
      const base = `${slug}-copy`;
      let next = base; let n = 2;
      while (taken.has(next)) { next = `${base}-${n}`; n += 1; }
      try {
        const g = JSON.parse(raw!);
        g.name = next;
        localStorage.setItem(draftKey(next), JSON.stringify(g));
      } catch { /* ignore */ }
      return { open: [...prev.open, next], active: next };
    });
  }, [setTabsState]);

  // Delete: remove the saved graph from the server, drop the draft, close the tab.
  const deleteTab = useCallback(async (slug: string) => {
    try { await fetch(`/api/graphs/${encodeURIComponent(slug)}`, { method: "DELETE" }); }
    catch { /* ignore */ }
    closeTab(slug);
  }, [closeTab]);

  // build the tab list for WorkflowTabs (slug identity + dirty flag for active).
  const tabs: WorkflowTab[] = useMemo(() => openSlugs.map((slug) => ({
    slug,
    dirty: slug === activeSlug ? graph.dirty : false,
  })), [openSlugs, activeSlug, graph.dirty]);

  const openSavedWorkflows = useCallback(() => {
    setRails((p) => p.library === "open" ? p : { ...p, library: "open" });
  }, [setRails]);

  // ── rail callbacks ──
  // The brand logo button no longer toggles a mode: both Workflows and
  // Node Library always render together. Click opens the rail when closed.
  const handleBrandClick = useCallback(() => {
    setRails((prev) => prev.library === "open" ? prev : { ...prev, library: "open" });
  }, [setRails]);

  const handleLibraryClose = useCallback(() => {
    setRails((prev) => ({ ...prev, library: "closed" }));
  }, [setRails]);

  // imperative bridge: the Canvas exposes its fit-view through this ref.
  const fitRef = useRef<(() => void) | null>(null);
  const beginRename = useCallback((id: string) => {
    select(id);
    setRenameTarget({ id, nonce: Date.now() });
  }, [select]);
  const beginGroupRename = useCallback((id: string) => {
    setRenameGroupTarget({ id, nonce: Date.now() });
  }, []);


  // ── keyboard shortcuts ──
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      const target = e.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);

      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
        return;
      }
      if (e.key === "Escape") {
        setPaletteOpen(false);
        setMenu(null);
        setProblemsOpen(false);
        return;
      }
      if (typing) return; // let fields own their keys

      if (mod && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void putGraph();
      } else if (mod && e.key.toLowerCase() === "z" && !e.shiftKey) {
        e.preventDefault();
        undo();
      } else if (mod && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) {
        e.preventDefault();
        redo();
      } else if (mod && e.key.toLowerCase() === "c") {
        copySelection();
      } else if (mod && e.key.toLowerCase() === "x") {
        cut();
      } else if (mod && e.key.toLowerCase() === "v") {
        paste(lastFlowPos.current);
      } else if (mod && e.key.toLowerCase() === "d") {
        e.preventDefault();
        if (selectedId) duplicateNode(selectedId);
      } else if (mod && e.key.toLowerCase() === "a") {
        e.preventDefault();
        selectAll();
      } else if (e.key === "Delete" || e.key === "Backspace") {
        if (selectedIds.length) {
          e.preventDefault();
          requestDelete(selectedIds, deleteSelection);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [putGraph, undo, redo, copySelection, cut, paste, duplicateNode, selectAll, deleteSelection, requestDelete, selectedIds, selectedId, selectedIds.length]);

  // ── help: one list feeds both the top bar's Help menu and the palette ──
  const copyDiagnostics = useCallback(async () => {
    const text = diagnosticsText({ info: version, userAgent: navigator.userAgent, nodeCount: nodes.length });
    const ok = await copyText(text);
    socket.notice(
      ok ? "diagnostics copied to the clipboard" : "could not copy diagnostics: the browser refused clipboard access",
      ok ? "ok" : "warn",
    );
  }, [version, nodes.length, socket.notice]);

  const helpActions: PaletteAction[] = useMemo(() => {
    const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
    const os = osName(nav.userAgent, nav.userAgentData?.platform || nav.platform);
    return [
      { id: "help-docs", group: "Help", label: "Documentation", hint: "boltjar.link/docs", icon: "book-outline", run: () => openExternal(DOCS_URL) },
      { id: "help-bug", group: "Help", label: "Report a bug", hint: "open an issue on GitHub", icon: "bug-outline", run: () => openExternal(bugReportUrl(version, os)) },
      { id: "help-feedback", group: "Help", label: "Send feedback", hint: "share an idea on GitHub", icon: "chatbubble-ellipses-outline", run: () => openExternal(FEEDBACK_URL) },
      { id: "help-diagnostics", group: "Help", label: "Copy diagnostics", hint: "version, platform, browser, node count", icon: "clipboard-outline", run: () => void copyDiagnostics() },
      { id: "help-support", group: "Help", label: "Support Boltjar", hint: "GitHub Sponsors", icon: "heart-outline", run: () => openExternal(SPONSOR_URL) },
      { id: "help-about", group: "Help", label: "About", hint: version ? `Boltjar ${version.version}` : "Boltjar", icon: "information-circle-outline", run: () => setAboutOpen(true) },
    ];
  }, [version, copyDiagnostics]);

  // the menu groups the links, the diagnostics, then the project itself.
  const helpItems: MenuItem[] = useMemo(
    () => helpActions.map((a) => ({
      id: a.id,
      label: a.label,
      icon: a.icon,
      separatorBefore: a.id === "help-diagnostics" || a.id === "help-support",
      run: a.run,
    })),
    [helpActions],
  );

  // ── palette actions (adapted to the power model) ──
  const paletteActions: PaletteAction[] = useMemo(
    () => [
      ...(socket.power === "off"
        ? [{ id: "on", label: "Power on", hint: "validate + start live", icon: "power", run: () => void powerOn() }]
        : [{ id: "off", label: "Power off", hint: "stop the runtime", icon: "power-outline", run: powerOff }]),
      ...(socket.power === "on" ? [{ id: "restart", label: "Save & Restart", hint: "apply live edits", icon: "refresh-outline", run: () => void restart() }] : []),
      { id: "save", label: "Save graph", hint: "PUT /api/graphs", icon: "save-outline", kbd: mod("S"), run: () => void putGraph() },
      { id: "connections", label: "Open Connections", hint: "manage providers and secrets", icon: "git-network-outline", run: () => setConnectionsOpen(true) },
      { id: "reset", label: "Reset to default graph", hint: "discard local edits", icon: "refresh-outline", run: () => { try { if (activeSlug) localStorage.removeItem(draftKey(activeSlug)); } catch { /* ignore */ } window.location.reload(); } },
      { id: "undo", label: "Undo", hint: "step back", icon: "arrow-undo-outline", kbd: mod("Z"), run: undo },
      { id: "redo", label: "Redo", hint: "step forward", icon: "arrow-redo-outline", kbd: mod("Y"), run: redo },
      { id: "clear", label: "Clear console", hint: "empty the log feed", icon: "trash-outline", run: socket.clearLog },
      ...helpActions,
    ],
    [socket.power, socket.clearLog, powerOn, powerOff, restart, putGraph, undo, redo, activeSlug, helpActions],
  );

  const selectedNode = nodes.find((n) => n.id === selectedId) ?? null;

  const editorCtx = useMemo(
    () => ({
      defs,
      models,
      modelsMeta,
      reloadModels,
      refreshModels,
      power: socket.power,
      nodeStatus: socket.nodeStatus,
      liveValues: socket.liveValues,
      valueHistory: socket.valueHistory,
      liveEdges: socket.liveEdges,
      chats: socket.chats,
      connectedInputs,
      connectedOutputs,
      inboundSources,
      problemsByNode,
      disabledIds,
      renameTarget,
      connecting,
      renameNode,
      updateConfig,
      setLlmModel,
      promoteParam,
      unpromoteParam,
      promoteWidget,
      unpromoteWidget,
      sendChat: socket.chat,
      fire: socket.fire,
      wirelessChannels,
      wirelessInOwners,
      effectiveOutput,
      openConnections: () => setConnectionsOpen(true),
      storeKeyForInput: (nodeId: string, port: string): string | null => {
        const wire = (inboundSources.get(nodeId) ?? []).find((w) => w.dstPort === port);
        if (!wire) return null;
        const src = nodes.find((n) => n.id === wire.src);
        if (!src) return null;
        const cfg = (src.data?.config ?? {}) as Record<string, unknown>;
        const keyField = port === "db" ? "db_key" : "kv_key";
        return String(cfg[keyField] ?? src.id);
      },
    }),
    [
      defs,
      models,
      modelsMeta,
      reloadModels,
      refreshModels,
      socket.power,
      socket.nodeStatus,
      socket.liveValues,
      socket.valueHistory,
      socket.liveEdges,
      socket.chats,
      socket.chat,
      socket.fire,
      wirelessChannels,
      wirelessInOwners,
      effectiveOutput,
      connectedInputs,
      connectedOutputs,
      inboundSources,
      problemsByNode,
      disabledIds,
      renameTarget,
      connecting,
      renameNode,
      updateConfig,
      setLlmModel,
      promoteParam,
      unpromoteParam,
      promoteWidget,
      unpromoteWidget,
      nodes,
    ],
  );

  const rejectionReason = useMemo(() => {
    if (!rejection) return null;
    if (Date.now() - rejection.at > 2600) return null;
    return rejection.reason;
  }, [rejection]);
  const [, force] = useState(0);
  useEffect(() => {
    if (!rejection) return;
    const t = window.setTimeout(() => force((n) => n + 1), 2700);
    return () => window.clearTimeout(t);
  }, [rejection]);

  // surface the problems panel automatically when an `invalid` arrives via ws
  useEffect(() => {
    if (socket.problems.length > 0) {
      setProblems(socket.problems);
      setProblemsOpen(true);
    }
  }, [socket.problems, setProblems]);

  return (
    <EditorProvider value={editorCtx}>
      <div
        className="app"
        data-lib={rails.library}
      >
        <CommandBar
          graphName={graphName}
          tabs={tabs}
          activeSlug={activeSlug}
          tabsStatus={tabsStatus}
          onActivateTab={activateTab}
          onCloseTab={closeTab}
          onRenameTab={renameTab}
          onCloneTab={cloneTab}
          onDeleteTab={deleteTab}
          onReorderTabs={reorderTabs}
          onActivateTabFromOverflow={activateTabFromOverflow}
          dirty={dirty}
          draft={draft}
          power={socket.power}
          connected={socket.connected}
          phase={phase}
          primary={primary}
          nodeCount={nodes.length}
          liveCount={liveCount}
          eventsPerSec={socket.counters.eventsPerSec}
          problemCount={problems.length}
          canUndo={canUndo}
          canRedo={canRedo}
          saving={saving}
          libraryRailOpen={rails.library === "open"}
          onPrimary={() => void onPrimary()}
          onOff={powerOff}
          onRestart={() => void restart()}
          onUndo={undo}
          onRedo={redo}
          onOpenPalette={() => setPaletteOpen(true)}
          onShowProblems={() => setProblemsOpen(true)}
          onOpenConnections={() => setConnectionsOpen(true)}
          onBrandClick={handleBrandClick}
          helpOpen={helpAnchor !== null}
          onOpenHelp={(r) => setHelpAnchor({ x: r.right, y: r.bottom + 6 })}
          onCloseHelp={() => setHelpAnchor(null)}
        />

        <div className={`library-rail${rails.library === "closed" ? " rail-closed" : ""}`}>
          {/* Two-pane left rail: Workflows on top (1/3 of the height, own
              scroll), Node Library below (2/3, own scroll). No modal toggle. */}
          <div className="rail-workflows">
            <SavedWorkflowsPanel
              onOpenTab={openTab}
              onNewWorkflow={newWorkflow}
              onRenameWorkflow={renameTab}
              onCloneWorkflow={cloneTab}
              onDeleteWorkflow={deleteTab}
              openSlugs={openSlugs}
              onCloseRail={handleLibraryClose}
            />
          </div>
          <div className="rail-library">
            <NodeLibrary
              defs={defList}
              presets={PRESETS}
              loading={defsLoading}
              error={defsError}
              onAdd={handleAddAtCenter}
              onAddPreset={handleAddPreset}
              onClose={handleLibraryClose}
            />
          </div>
          {rails.library === "closed" && (
            <button
              className="rail-tab left"
              onClick={() => setRails((p) => ({ ...p, library: "open" }))}
              title="Open library"
            >
              <span className="rail-tab-chev">›</span>
            </button>
          )}
        </div>

        <div className="canvas-area">
          {activeSlug ? (
            <ReactFlowProvider>
              <Canvas
                nodes={nodes}
                edges={edges}
                defs={defs}
                groups={groups}
                onRenameGroup={renameGroup}
                onRecolorGroup={recolorGroup}
                onGroupDragStart={groupDragStart}
                onMoveGroup={moveGroup}
                onAddToGroup={addToGroup}
                renameGroupTarget={renameGroupTarget}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                onReconnectEdge={reconnectEdge}
                isValidConnection={isValidConnection}
                onAddNode={addNodeOfType}
                onPaneClick={() => {
                  select(null);
                  setMenu(null);
                }}
                onMoveEnd={setZoom}
                onCursorMove={(x, y) => setCursor({ x, y })}
                onContextRequest={onContextRequest}
                onConnectingChange={setConnecting}
                rejectionReason={rejectionReason}
                loading={graphLoading}
                graphName={graphName}
                liveCount={liveCount}
                eventsPerSec={socket.counters.eventsPerSec}
                problemCount={problems.length}
                onShowProblems={() => setProblemsOpen(true)}
                onOpenPalette={() => { setPaletteTypeFilter(null); setPaletteOpen(true); }}
                onDropInEmpty={onDropInEmpty}
                primary={primary}
                saving={saving}
                onPrimary={() => void onPrimary()}
                fitRef={fitRef}
              />
            </ReactFlowProvider>
          ) : (
            <div className="wf-tabs-empty">
              <div className="wf-tabs-empty-title">No workflow open</div>
              <div className="wf-tabs-empty-hint">
                Open one from the Saved Workflows panel.
              </div>
              <button
                type="button"
                className="wf-tabs-empty-btn"
                onClick={openSavedWorkflows}
              >
                Browse saved workflows
              </button>
            </div>
          )}
        </div>

        {/* StatusBar is a grid child of .app (grid-area: status), so it must
            live INSIDE the .app wrapper for the grid placement to work. */}
        <StatusBar
          log={socket.log}
          running={socket.power === "on"}
          eventsPerSec={socket.counters.eventsPerSec}
          inFlight={liveCount}
          nodeCount={nodes.length}
          cursor={cursor}
          zoom={zoom}
          dirty={dirty}
          lastSaved={lastSaved}
          onClear={socket.clearLog}
        />
      </div>

      {problemsOpen && problems.length > 0 && (
        <ProblemsPanel
          problems={problems}
          onGoToNode={(id) => {
            select(id);
            setProblemsOpen(false);
          }}
          onClose={() => setProblemsOpen(false)}
        />
      )}

      {menu && (
        <ContextMenu
          x={menu.screenX}
          y={menu.screenY}
          items={menuItems}
          defs={menu.kind === "canvas" ? defList : menu.kind === "edge" ? defList : undefined}
          searchTitle={menu.kind === "edge" ? "Insert node on wire" : "Add node"}
          onPickNode={
            menu.kind === "canvas"
              ? (typeId) => addNodeOfType(typeId, menu.flow)
              : menu.kind === "edge" && menu.targetId
                ? (typeId) => insertOnEdge(menu.targetId!, typeId, menu.flow)
                : undefined
          }
          onClose={() => setMenu(null)}
        />
      )}

      {helpAnchor && (
        <ContextMenu
          x={helpAnchor.x}
          y={helpAnchor.y}
          align="end"
          label="Help"
          items={helpItems}
          onClose={() => setHelpAnchor(null)}
        />
      )}

      {aboutOpen && (
        <NodeModal title="About Boltjar" icon="information-circle-outline" onClose={() => setAboutOpen(false)}>
          <div className="about-modal">
            <img className="about-logo" src={logoUrl} alt="Boltjar" draggable={false} />
            <p className="about-version">{version ? `Version ${version.version}` : "Version unknown"}</p>
          </div>
        </NodeModal>
      )}

      {paletteOpen && (
        <CommandPalette
          defs={defList}
          nodes={nodes}
          actions={paletteActions}
          onAddNode={handleAddAtCenter}
          onGoToNode={(id) => select(id)}
          onClose={() => { setPaletteOpen(false); setPaletteTypeFilter(null); dropPinRef.current = null; }}
          typeFilter={paletteTypeFilter}
        />
      )}

      <ConnectionsWindow
        open={connectionsOpen}
        onClose={() => {
          setConnectionsOpen(false);
          // a key added or removed changes which models can run.
          void reloadModels();
        }}
      />

      {pendingDelete && (
        <NodeModal
          title="Delete store node"
          icon="trash-outline"
          onClose={() => setPendingDelete(null)}
        >
          <div className="confirm-modal">
            <p>
              Deleting <b>{pendingDelete.stores.map((s) => s.id).join(", ")}</b> also
              <b> permanently erases its data file on disk</b>. This cannot be undone.
            </p>
            <div className="confirm-actions">
              <button type="button" className="confirm-cancel" onClick={() => setPendingDelete(null)}>Cancel</button>
              <button type="button" className="confirm-del" onClick={async () => {
                const { run, stores } = pendingDelete;
                setPendingDelete(null);
                await Promise.all(stores.map(async (s) => {
                  // snapshot the db schema (columns only) so undo can bring the
                  // empty tables back; then erase the data file from disk.
                  if (s.kind === "db") {
                    try {
                      const r = await fetch(`/api/db/${encodeURIComponent(s.key)}/schema`);
                      if (r.ok) {
                        const data = await r.json();
                        erasedSchemasRef.current.set(s.id, { key: s.key, tables: data.schema ?? [] });
                      }
                    } catch { /* no snapshot: undo will bring back an empty store */ }
                  }
                  await fetch(`/api/store/${s.kind}/${encodeURIComponent(s.key)}`, { method: "DELETE" }).catch(() => {});
                }));
                run();
              }}>Delete &amp; erase data</button>
            </div>
          </div>
        </NodeModal>
      )}
    </EditorProvider>
  );
}
