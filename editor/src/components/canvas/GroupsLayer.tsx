// ============================================================================
// GroupsLayer: visual node groups. Renders behind the nodes inside a
// ViewportPortal (so it pans/zooms with the canvas). Each group's rectangle is
// DERIVED from its members' bounding box + padding, so it auto-resizes as nodes
// move. The body is translucent (overlapped nodes show through); the title bar is
// the only interactive part: drag to move all members, double-click to rename,
// click the swatch to recolour, × to ungroup. The runtime never sees groups.
// ============================================================================
import { useEffect, useRef, useState } from "react";
import { ViewportPortal, useReactFlow } from "@xyflow/react";
import type { NodeGroup } from "../../types/protocol";

/** group tint palette: key -> hex (matches useGraph's GROUP_PALETTE keys). */
export const GROUP_COLORS: Record<string, string> = {
  slate: "#64748b",
  indigo: "#818cf8",
  teal: "#2dd4bf",
  amber: "#fbbf24",
  rose: "#fb7185",
  violet: "#c084fc",
};
const PADDING = 22;
const BAR = 30;

/** A node-like just needs a position + a measured/declared size. */
interface RectNode {
  position: { x: number; y: number };
  measured?: { width?: number; height?: number };
  width?: number | null;
  height?: number | null;
}

/**
 * The group's rectangle, DERIVED from its members' live geometry (bbox +
 * padding + title bar). ONE source of truth: both the rendered box and the
 * drag-in hit-testing read this, so they can never drift. Returns null when no
 * member has measured geometry yet.
 */
export function groupRect(
  members: string[],
  getNode: (id: string) => RectNode | undefined,
): { x: number; y: number; width: number; height: number } | null {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const id of members) {
    const n = getNode(id);
    if (!n) continue;
    const w = (n.measured?.width ?? n.width ?? 240) as number;
    const h = (n.measured?.height ?? n.height ?? 120) as number;
    minX = Math.min(minX, n.position.x);
    minY = Math.min(minY, n.position.y);
    maxX = Math.max(maxX, n.position.x + w);
    maxY = Math.max(maxY, n.position.y + h);
  }
  if (!isFinite(minX)) return null;
  return {
    x: minX - PADDING,
    y: minY - PADDING - BAR,
    width: maxX - minX + PADDING * 2,
    height: maxY - minY + PADDING * 2 + BAR,
  };
}

interface GroupsLayerProps {
  groups: NodeGroup[];
  /** the group currently under a dragged node (highlighted as a drop target). */
  dropTargetId?: string | null;
  /** when {id} matches a group, that group enters inline-rename (menu "Rename"). */
  renameTarget?: { id: string; nonce: number } | null;
  onRename: (id: string, title: string) => void;
  onRecolor: (id: string, color: string) => void;
  onDragStart: () => void;
  onMove: (id: string, dx: number, dy: number) => void;
  onContext: (groupId: string, e: React.MouseEvent) => void;
}

export function GroupsLayer({ groups, dropTargetId, renameTarget, onRename, onRecolor, onDragStart, onMove, onContext }: GroupsLayerProps) {
  const rf = useReactFlow();
  if (groups.length === 0) return null;
  return (
    <ViewportPortal>
      {groups.map((g) => (
        <GroupBox
          key={g.id}
          group={g}
          rf={rf}
          dropTarget={g.id === dropTargetId}
          renameTarget={renameTarget}
          onRename={onRename}
          onRecolor={onRecolor}
          onDragStart={onDragStart}
          onMove={onMove}
          onContext={onContext}
        />
      ))}
    </ViewportPortal>
  );
}

function GroupBox({
  group, rf, dropTarget, renameTarget, onRename, onRecolor, onDragStart, onMove, onContext,
}: { group: NodeGroup; rf: ReturnType<typeof useReactFlow>; dropTarget: boolean } & Omit<GroupsLayerProps, "groups" | "dropTargetId">) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(group.title);
  const [picker, setPicker] = useState(false);
  const drag = useRef<{ x: number; y: number } | null>(null);
  useEffect(() => { if (!editing) setDraft(group.title); }, [group.title, editing]);
  // the group menu's "Rename" raises this target -> enter inline edit (mirrors
  // WorkflowNode's rename-from-menu). The nonce re-fires even for the same id.
  useEffect(() => {
    if (renameTarget && renameTarget.id === group.id) setEditing(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renameTarget, group.id]);

  // derive the bounding box from the members' live geometry (shared with the
  // drag-in hit-test so the two never drift).
  const rect = groupRect(group.members, (id) => rf.getNode(id) as RectNode | undefined);
  if (!rect) return null;
  const { x, y, width, height } = rect;
  const hex = GROUP_COLORS[group.color] ?? GROUP_COLORS.slate;

  // drag the bar to translate every member (flow-space delta = screen / zoom).
  const onPointerDown = (e: React.PointerEvent) => {
    if (editing) return;
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY };
    onDragStart();
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const zoom = rf.getViewport().zoom || 1;
    const dx = (e.clientX - drag.current.x) / zoom;
    const dy = (e.clientY - drag.current.y) / zoom;
    if (dx === 0 && dy === 0) return;
    drag.current = { x: e.clientX, y: e.clientY };
    onMove(group.id, dx, dy);
  };
  const endDrag = (e: React.PointerEvent) => {
    drag.current = null;
    try { (e.target as HTMLElement).releasePointerCapture(e.pointerId); } catch { /* ignore */ }
  };

  const commitName = () => { setEditing(false); const t = draft.trim(); if (t && t !== group.title) onRename(group.id, t); };

  return (
    <div
      className={`grp-box ${dropTarget ? "drop-target" : ""}`}
      style={{
        position: "absolute", left: x, top: y, width, height,
        ["--gc" as string]: hex,
      } as React.CSSProperties}
    >
      <div
        className="grp-bar nodrag nowheel nopan"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        // The bar lives in a ViewportPortal, so a dblclick here bubbles up the
        // React tree to ReactFlow's onDoubleClick (which opens the command
        // palette). Swallow it so double-clicking the group never opens the
        // palette; the name's own handler (below) still starts the rename.
        onDoubleClick={(e) => e.stopPropagation()}
        onContextMenu={(e) => onContext(group.id, e)}
      >
        <button
          type="button"
          className="grp-swatch"
          title="group colour"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => setPicker((p) => !p)}
        />
        {editing ? (
          <input
            className="grp-name-edit nodrag"
            value={draft}
            autoFocus
            onPointerDown={(e) => e.stopPropagation()}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commitName}
            onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") commitName(); if (e.key === "Escape") { setDraft(group.title); setEditing(false); } }}
          />
        ) : (
          <span className="grp-name" onDoubleClick={(e) => { e.stopPropagation(); setEditing(true); }} title="double-click to rename">{group.title}</span>
        )}
        {dropTarget && <span className="grp-drop-hint">add to group</span>}
        {picker && (
          <div className="grp-picker nodrag" onPointerDown={(e) => e.stopPropagation()}>
            {Object.entries(GROUP_COLORS).map(([key, c]) => (
              <button
                key={key}
                type="button"
                className={`grp-swatch ${key === group.color ? "active" : ""}`}
                style={{ ["--gc" as string]: c } as React.CSSProperties}
                onClick={() => { onRecolor(group.id, key); setPicker(false); }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
