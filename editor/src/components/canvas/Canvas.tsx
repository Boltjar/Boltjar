// ============================================================================
// Canvas: the React Flow viewport with the blueprint background, the custom
// typed node + edge renderers, drag-and-drop from the library, the minimap, and
// the bottom-right zoom cluster. Owns the connection-derivation maps (which
// handles are wired, growable counts) that the node renderer reads via context.
// ============================================================================
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  Background,
  BackgroundVariant,
  MiniMap,
  ReactFlow,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type EdgeTypes,
  type NodeChange,
  type NodeTypes,
  type OnConnectStartParams,
  type ReactFlowInstance,
} from "@xyflow/react";
import type { WFEdge, WFNode } from "../../lib/graphAdapter";
import { WorkflowNode } from "./WorkflowNode";
import { TypedEdge } from "./TypedEdge";
import { GroupsLayer, groupRect } from "./GroupsLayer";
import { Icon } from "../../lib/icons";
import { functionColorVar } from "../../lib/kinds";
import { typeColorVar } from "../../lib/types";
import { mod } from "../../lib/platform";
import { panHintCenter } from "../../lib/panHint";
import type { NodeDef } from "../../types/protocol";

const nodeTypes: NodeTypes = { workflow: WorkflowNode };
const edgeTypes: EdgeTypes = { typed: TypedEdge };

/** What the canvas reports when something is right-clicked. */
export interface CanvasMenuRequest {
  kind: "node" | "canvas" | "edge" | "group";
  /** target node or edge id (undefined for the bare canvas). */
  targetId?: string;
  /** viewport (screen) coordinates to anchor the menu. */
  screenX: number;
  screenY: number;
  /** the flow-space position under the cursor (for placing new nodes). */
  flow: { x: number; y: number };
}

interface CanvasProps {
  nodes: WFNode[];
  edges: WFEdge[];
  defs: Map<string, NodeDef>;
  groups: import("../../types/protocol").NodeGroup[];
  onRenameGroup: (id: string, title: string) => void;
  onRecolorGroup: (id: string, color: string) => void;
  onGroupDragStart: () => void;
  onMoveGroup: (id: string, dx: number, dy: number) => void;
  /** drag-in: drop node(s) over a group to add them as members. */
  onAddToGroup: (nodeIds: string[], groupId: string) => void;
  /** right-click a group's bar -> open the group context menu (recolour/ungroup). */
  renameGroupTarget: { id: string; nonce: number } | null;
  onNodesChange: (c: NodeChange<WFNode>[]) => void;
  onEdgesChange: (c: EdgeChange<WFEdge>[]) => void;
  onConnect: (c: Connection) => void;
  onReconnectEdge: (oldEdge: WFEdge, c: Connection) => void;
  isValidConnection: (c: Connection | WFEdge) => boolean;
  onAddNode: (typeId: string, pos: { x: number; y: number }) => void;
  onPaneClick: () => void;
  onMoveEnd: (zoom: number) => void;
  onCursorMove: (x: number, y: number) => void;
  onContextRequest: (req: CanvasMenuRequest) => void;
  /** report a drag-from-port in progress (its source type) so targets can dim. */
  onConnectingChange: (info: { fromType: string; fromId: string } | null) => void;
  /** the canvas writes its fit-view callback here so the chrome can trigger it. */
  fitRef?: React.MutableRefObject<(() => void) | null>;
  rejectionReason: string | null;
  loading: boolean;
  graphName: string;
  /** runtime stats, surfaced inline on the canvas overlay so the top bar
   *  stays minimal. liveCount: nodes currently emitting; eventsPerSec: 1s
   *  rolling event rate; problemCount: validation problems. */
  liveCount: number;
  eventsPerSec: number;
  problemCount: number;
  onShowProblems: () => void;
  /** double-click on an empty pane opens the command palette (replaces the
   *  default React Flow zoom-on-doubleclick, which is unintuitive). */
  onOpenPalette: () => void;
  /** dropping a wire on empty space opens the palette filtered to the nodes
   *  whose input (when the drag started from an output) or output (when the
   *  drag started from an input) is compatible with the source's data type.
   *  The drop position (flow coords) is forwarded so the picked node lands
   *  where the user let go. */
  onDropInEmpty: (info: { type: string; direction: "input" | "output"; pos: { x: number; y: number } }) => void;
  /** the adaptive primary action surfaced as a floating button at the top
   *  center of the canvas: "Save" when off+dirty, "Save & Restart" when
   *  on+draft. Click invokes onPrimary; saving renders the busy state. */
  primary: "save" | "on" | "save-restart" | "none";
  saving: boolean;
  onPrimary: () => void;
}

export function Canvas(props: CanvasProps) {
  const {
    nodes,
    edges,
    defs,
    groups,
    onRenameGroup,
    onRecolorGroup,
    onGroupDragStart,
    onMoveGroup,
    onAddToGroup,
    renameGroupTarget,
    onNodesChange,
    onEdgesChange,
    onConnect,
    onReconnectEdge,
    isValidConnection,
    onAddNode,
    onPaneClick,
    onMoveEnd,
    onCursorMove,
    onContextRequest,
    onConnectingChange,
    fitRef,
    rejectionReason,
    loading,
    liveCount,
    eventsPerSec,
    problemCount,
    onShowProblems,
    onOpenPalette,
    onDropInEmpty,
    primary,
    saving,
    onPrimary,
    graphName,
  } = props;

  const wrapperRef = useRef<HTMLDivElement>(null);
  const rf = useReactFlow<WFNode, WFEdge>();
  const [instance, setInstance] = useState<ReactFlowInstance<WFNode, WFEdge> | null>(null);
  const [hintHidden, setHintHidden] = useState(false);
  const connectStart = useRef<OnConnectStartParams | null>(null);
  // when a drag-from-port produces a real edge (onConnect fires), set this to
  // true so onConnectEnd does NOT treat the drop as "in empty space".
  const madeConnection = useRef(false);
  // the source type while a drag is in progress (text/event/audio/...). Drives
  // the dashed wire's color (CSS var --connecting-color) AND the type-filter
  // for the palette when the user drops on empty space.
  const [dragType, setDragType] = useState<string | null>(null);
  // the handle SIDE the drag started from: "source" = output -> looking for
  // an INPUT target; "target" = input -> looking for an OUTPUT target.
  const dragSideRef = useRef<"source" | "target">("source");
  // the group currently under a dragged node (drag-in highlight).
  const [dropGroupId, setDropGroupId] = useState<string | null>(null);

  // Which group (if any) the dragged node(s) land on: a group whose derived rect
  // contains the CENTER of a dragged node that is NOT already a member. The
  // member check means dragging a node WITHIN its own group never re-triggers
  // (its box just expands, which is the intended drag-out behaviour).
  const detectDropGroup = useCallback(
    (dragged: WFNode[]): string | null => {
      for (const g of groups) {
        const rect = groupRect(g.members, (id) => rf.getNode(id));
        if (!rect) continue;
        for (const nd of dragged) {
          if (g.members.includes(nd.id)) continue;
          const live = rf.getNode(nd.id) ?? nd;
          const w = (live.measured?.width ?? live.width ?? 240) as number;
          const h = (live.measured?.height ?? live.height ?? 120) as number;
          const cx = live.position.x + w / 2;
          const cy = live.position.y + h / 2;
          if (cx >= rect.x && cx <= rect.x + rect.width && cy >= rect.y && cy <= rect.y + rect.height) {
            return g.id;
          }
        }
      }
      return null;
    },
    [groups, rf],
  );

  // fade the pan hint after a moment
  useEffect(() => {
    const t = window.setTimeout(() => setHintHidden(true), 2800);
    return () => window.clearTimeout(t);
  }, []);

  // the pan hint shares the bottom row with the React Flow credit and the zoom
  // cluster: it sits in the free stretch between them (lib/panHint), measured
  // again whenever the canvas or the pill changes size, and hides when a narrow
  // canvas leaves it no room. null centre = no room.
  const hintRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef<HTMLDivElement>(null);
  const [hintX, setHintX] = useState<number | null | undefined>(undefined);
  useLayoutEffect(() => {
    const canvas = wrapperRef.current;
    const hint = hintRef.current;
    const zoom = zoomRef.current;
    if (!canvas || !hint || !zoom) return;
    const place = () => {
      const box = canvas.getBoundingClientRect();
      const credit = canvas.querySelector(".canvas-credit")?.getBoundingClientRect();
      const tokens = getComputedStyle(canvas);
      setHintX(panHintCenter({
        width: box.width,
        hint: hint.offsetWidth,
        left: credit ? credit.right - box.left : 0,
        right: zoom.getBoundingClientRect().left - box.left,
        gap: parseFloat(tokens.getPropertyValue("--space-3")) || 0,
        inset: parseFloat(tokens.getPropertyValue("--space-4")) || 0,
      }));
    };
    place();
    const ro = new ResizeObserver(place);
    ro.observe(canvas);
    ro.observe(hint);
    return () => ro.disconnect();
  }, []);

  // fit to the graph once it first loads
  const fittedRef = useRef(false);
  useEffect(() => {
    if (!fittedRef.current && instance && nodes.length > 0) {
      fittedRef.current = true;
      window.setTimeout(() => instance.fitView({ padding: 0.22, duration: 400, maxZoom: 1.1 }), 60);
    }
  }, [instance, nodes.length]);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const typeId = event.dataTransfer.getData("application/boltjar-node");
      if (!typeId) return;
      const pos = rf.screenToFlowPosition({ x: event.clientX, y: event.clientY });
      onAddNode(typeId, pos);
    },
    [rf, onAddNode],
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }, []);

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!instance) return;
      const p = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      onCursorMove(Math.round(p.x), Math.round(p.y));
    },
    [instance, rf, onCursorMove],
  );

  const flowAt = useCallback(
    (clientX: number, clientY: number) => rf.screenToFlowPosition({ x: clientX, y: clientY }),
    [rf],
  );

  const onNodeContextMenu = useCallback(
    (e: React.MouseEvent, node: WFNode) => {
      e.preventDefault();
      onContextRequest({
        kind: "node",
        targetId: node.id,
        screenX: e.clientX,
        screenY: e.clientY,
        flow: flowAt(e.clientX, e.clientY),
      });
    },
    [onContextRequest, flowAt],
  );

  // Right-clicking a multi-selection fires onSelectionContextMenu (NOT
  // onNodeContextMenu, which only fires for a single node). Forward it as a
  // "node" request anchored on any selected node so the menu's `multi` branch
  // (selectedIds.length > 1) lights up and acts on the whole selection.
  const onSelectionContextMenu = useCallback(
    (e: React.MouseEvent, sel: WFNode[]) => {
      e.preventDefault();
      onContextRequest({
        kind: "node",
        targetId: sel[0]?.id,
        screenX: e.clientX,
        screenY: e.clientY,
        flow: flowAt(e.clientX, e.clientY),
      });
    },
    [onContextRequest, flowAt],
  );

  // Right-click a group's bar -> the group context menu (recolour / ungroup /
  // rename). The bar lives in a ViewportPortal, so without this it would fall
  // through to the browser's native menu.
  const onGroupContext = useCallback(
    (groupId: string, e: React.MouseEvent) => {
      e.preventDefault();
      onContextRequest({
        kind: "group",
        targetId: groupId,
        screenX: e.clientX,
        screenY: e.clientY,
        flow: flowAt(e.clientX, e.clientY),
      });
    },
    [onContextRequest, flowAt],
  );

  const onEdgeContextMenu = useCallback(
    (e: React.MouseEvent, edge: WFEdge) => {
      e.preventDefault();
      onContextRequest({
        kind: "edge",
        targetId: edge.id,
        screenX: e.clientX,
        screenY: e.clientY,
        flow: flowAt(e.clientX, e.clientY),
      });
    },
    [onContextRequest, flowAt],
  );

  const onPaneContextMenu = useCallback(
    (e: MouseEvent | React.MouseEvent) => {
      e.preventDefault();
      const me = e as React.MouseEvent;
      onContextRequest({
        kind: "canvas",
        screenX: me.clientX,
        screenY: me.clientY,
        flow: flowAt(me.clientX, me.clientY),
      });
    },
    [onContextRequest, flowAt],
  );

  const minimapNodeColor = useCallback(
    (n: WFNode) => {
      const def = defs.get(n.data.typeId);
      if (!def) return "var(--ink-ghost)";
      return functionColorVar(def);
    },
    [defs],
  );

  const fitView = useCallback(() => rf.fitView({ padding: 0.22, duration: 300, maxZoom: 1.1 }), [rf]);
  const zoomIn = useCallback(() => rf.zoomIn({ duration: 160 }), [rf]);
  const zoomOut = useCallback(() => rf.zoomOut({ duration: 160 }), [rf]);

  // expose fit-view to the chrome (the canvas context menu's "Fit view").
  useEffect(() => {
    if (fitRef) fitRef.current = fitView;
  }, [fitRef, fitView]);

  const nodeCount = nodes.length;
  void edges; // wire count is dropped from the overlay (low signal)
  // graphName is now surfaced by the WorkflowTabs strip above the canvas; the
  // overlay shows only the lightweight node/wire count.
  void graphName;

  return (
    <main
      className="canvas"
      ref={wrapperRef}
      onDrop={onDrop}
      onDragOver={onDragOver}
      onMouseMove={handleMouseMove}
      style={dragType ? ({ ["--connecting-color" as string]: typeColorVar(dragType) } as CSSProperties) : undefined}
    >
      <ReactFlow<WFNode, WFEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={(c) => { madeConnection.current = true; onConnect(c); }}
        onReconnect={(oldEdge, newConn) => onReconnectEdge(oldEdge, newConn)}
        isValidConnection={isValidConnection}
        onConnectStart={(_, params) => {
          connectStart.current = params;
          madeConnection.current = false;
          dragSideRef.current = params.handleType === "target" ? "target" : "source";
          // resolve the dragged port's type so compatible targets light up AND
          // the dashed wire renders in its data-type color.
          if (params.nodeId && params.handleId) {
            const def = defs.get(nodes.find((n) => n.id === params.nodeId)?.data.typeId ?? "");
            const t =
              params.handleType === "target"
                ? def?.inputs.find((p) => p.name === params.handleId)?.type ?? "any"
                : def?.outputs.find((p) => p.name === params.handleId)?.type ?? "any";
            setDragType(t);
            if (params.handleType === "source") onConnectingChange({ fromType: t, fromId: params.nodeId });
          }
        }}
        onConnectEnd={(event) => {
          const t = dragType;
          const side = dragSideRef.current;
          const made = madeConnection.current;
          connectStart.current = null;
          onConnectingChange(null);
          setDragType(null);
          // drop on empty space (no edge created): open the palette filtered
          // by the source type, with the drop position so the picked node
          // lands where the user let go.
          if (!made && t) {
            const e = event as MouseEvent;
            const pos = flowAt(e.clientX, e.clientY);
            // direction the user is LOOKING FOR on the new node:
            //   started from output (source) -> needs an INPUT target.
            //   started from input  (target) -> needs an OUTPUT target.
            const direction = side === "source" ? "input" : "output";
            onDropInEmpty({ type: t, direction, pos });
          }
        }}
        onInit={setInstance}
        onPaneClick={onPaneClick}
        onNodeContextMenu={onNodeContextMenu}
        onSelectionContextMenu={onSelectionContextMenu}
        onEdgeContextMenu={onEdgeContextMenu}
        onPaneContextMenu={onPaneContextMenu}
        // drag-in: highlight the group under the cursor while dragging,
        // and commit membership on drop. The third arg is every node being
        // dragged (the whole selection when a selected node is grabbed).
        onNodeDrag={(_, node, dragged) => setDropGroupId(detectDropGroup(dragged.length ? dragged : [node]))}
        onNodeDragStop={(_, node, dragged) => {
          const ids = dragged.length ? dragged : [node];
          const gid = detectDropGroup(ids);
          if (gid) onAddToGroup(ids.map((n) => n.id), gid);
          setDropGroupId(null);
        }}
        onSelectionDrag={(_, dragged) => setDropGroupId(detectDropGroup(dragged))}
        onSelectionDragStop={(_, dragged) => {
          const gid = detectDropGroup(dragged);
          if (gid) onAddToGroup(dragged.map((n) => n.id), gid);
          setDropGroupId(null);
        }}
        onMoveEnd={(_, viewport) => onMoveEnd(viewport.zoom)}
        snapToGrid
        snapGrid={[12, 12]}
        minZoom={0.25}
        maxZoom={2.2}
        zoomOnDoubleClick={false}
        onDoubleClick={(e) => {
          // open the command palette only when the dblclick lands on the bare
          // pane (not a node, not a wire). The Background / viewport carry the
          // `react-flow__pane` class; anything inside a .react-flow__node is a
          // node and should be ignored here (nodes have their OWN dblclick
          // handler that opens the inspector when it is closed).
          const t = e.target as HTMLElement;
          if (t.closest('.react-flow__node') || t.closest('.react-flow__edge')) return;
          onOpenPalette();
        }}
        defaultEdgeOptions={{ type: "typed" }}
        // React Flow is credited in Help > About with its licence; the canvas
        // corner carries the Boltjar credit instead (.canvas-credit below).
        proOptions={{ hideAttribution: true }}
        // App owns Delete/Backspace (App.requestDelete) so store-node deletes get
        // the confirm. Disabling RF's built-in delete avoids it wiping nodes first.
        deleteKeyCode={null}
        multiSelectionKeyCode={["Meta", "Control"]}
        nodesDraggable
        elevateNodesOnSelect
      >
        <Background
          variant={BackgroundVariant.Lines}
          gap={24}
          lineWidth={1}
          color="var(--grid)"
        />
        <Background
          id="major"
          variant={BackgroundVariant.Lines}
          gap={120}
          lineWidth={1}
          color="var(--grid-major)"
        />
        <MiniMap<WFNode>
          pannable
          zoomable
          nodeColor={minimapNodeColor}
          nodeStrokeColor="transparent"
          nodeBorderRadius={3}
          maskColor="rgba(7,10,15,0.6)"
        />
        <GroupsLayer
          groups={groups}
          dropTargetId={dropGroupId}
          renameTarget={renameGroupTarget}
          onRename={onRenameGroup}
          onRecolor={onRecolorGroup}
          onDragStart={onGroupDragStart}
          onMove={onMoveGroup}
          onContext={onGroupContext}
        />
      </ReactFlow>

      {/* floating primary action (top center of the canvas): "Save" or
          "Save & Restart" depending on power+draft state. Hidden when there
          is nothing to do (primary === "none" / "on"). */}
      {(primary === "save" || primary === "save-restart") && (
        <button
          className={`canvas-primary ${primary === "save" ? "save" : "restart"} ${saving ? "busy" : ""}`}
          onClick={onPrimary}
          title={primary === "save" ? "Save" : "Save & Restart"}
        >
          <Icon name={saving ? "sync-outline" : (primary === "save" ? "save-outline" : "refresh-outline")} />
          <span className="cp-label">
            {saving ? "saving…" : (primary === "save" ? "Save" : "Save & Restart")}
          </span>
        </button>
      )}

      {/* The old top-left stats overlay (nodes / live / ev-s) was redundant
          with the StatusBar at the bottom; both now live in the footer.
          The Problems chip stays on the canvas because it is an actionable
          alert that needs to be in the user's eyeline. */}
      {problemCount > 0 && (
        <div className="canvas-overlay">
          <button className="ti problems" onClick={onShowProblems} title="show problems">
            <Icon name="warning-outline" />
            <b>{problemCount}</b>
          </button>
        </div>
      )}

      {loading && <div className="canvas-loading">loading graph…</div>}

      {!loading && nodeCount === 0 && (
        <div className="canvas-loading">
          empty canvas · drag a node from the library or press {mod("K")}
        </div>
      )}

      <div className="canvas-credit">Designed by DKLRD</div>

      <div
        ref={hintRef}
        className={`pan-hint ${hintHidden ? "hide" : ""} ${hintX === null ? "no-room" : ""}`}
        style={typeof hintX === "number" ? { left: hintX } : undefined}
      >
        <Icon name="hand-left-outline" /> drag to pan · scroll to zoom
      </div>

      {rejectionReason && (
        <div className="reject-toast" key={rejectionReason + Date.now()}>
          <Icon name="close-circle" /> {rejectionReason}
        </div>
      )}

      <div className="zoom-ctrl" ref={zoomRef}>
        <button className="icon-btn" title="Zoom in" onClick={zoomIn}>
          <Icon name="add-outline" />
        </button>
        <button className="icon-btn" title="Fit graph" onClick={fitView}>
          <Icon name="scan-outline" />
        </button>
        <button className="icon-btn" title="Zoom out" onClick={zoomOut}>
          <Icon name="remove-outline" />
        </button>
      </div>
    </main>
  );
}
