// ============================================================================
// WorkflowTabs: the browser-like tab strip, rendered INSIDE the CommandBar
// (top chrome) where the graph name used to live. Each tab is an open workflow
// (a slug). The active tab gets a brighter background; the X closes it (Off
// via the per-tab ws, then drop from the open list).
//
// To add a new workflow the user opens the Saved Workflows panel via the
// brand logo (there is no '+' here, by design; the panel carries an
// explicit "New workflow" affordance instead).
//
// Overflow + drag-and-drop:
//   - At most MAX_VISIBLE slots render in the strip. With overflow, the last
//     slot becomes a single "More N" button; clicking opens a body-portaled
//     dropdown with one row per overflowed tab (status dot + name + close X).
//   - HTML5 drag-and-drop reorders tabs inside the strip, between strip and
//     dropdown, and within the dropdown. A 2px accent bar marks the drop slot.
//
// Visual rules: tokens only, no hardcoded colors, no single-side accent borders.
// ============================================================================
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../lib/icons";
import { ContextMenu } from "./ContextMenu";
import type { Power } from "../hooks/useRunSocket";
import { computeVisible, reorder } from "./WorkflowTabs.test.helper";

/** Maximum slots in the visible strip. With overflow, the LAST slot becomes
 *  the single "More N" trigger, so the actual rendered tab count caps at
 *  (MAX_VISIBLE - 1) + 1 = MAX_VISIBLE. */
const MAX_VISIBLE = 4;

/** dataTransfer key for drag-and-drop payload (the dragged tab's slug). */
const DRAG_TYPE = "text/plain";

export interface WorkflowTab {
  slug: string;
  /** True when this tab has unsaved local edits (drives the close-confirm gate). */
  dirty?: boolean;
}

interface WorkflowTabsProps {
  tabs: WorkflowTab[];
  activeSlug: string | null;
  /** {slug -> "on"|"off"} per the lightweight per-tab status sockets. */
  statusBySlug: Record<string, Power>;
  onActivate: (slug: string) => void;
  /** Close a tab: send Off over its ws and remove from the open list. */
  onClose: (slug: string) => void;
  /** Right-click "Rename": rename a tab's slug (prompts inline). */
  onRename: (slug: string, nextSlug: string) => void;
  /** Right-click "Clone": duplicate the workflow under a fresh slug. */
  onClone: (slug: string) => void;
  /** Right-click "Delete": permanently remove the saved graph + close the tab. */
  onDelete: (slug: string) => void;
  /** Commit a new tab order (permutation of current `tabs`). The host (App)
   *  validates and writes through to localStorage. */
  onReorder: (nextOrder: string[]) => void;
  /** When the user picks an overflowed tab from the More dropdown, the host
   *  brings that slug to position 0 AND activates it (so the active tab is
   *  always inside the visible group). */
  onActivateFromOverflow: (slug: string) => void;
}

/** Where a drop indicator should sit (between two items, before index `at`).
 *  `zone` distinguishes strip-vs-overflow drop targets; `at` is the insertion
 *  index INSIDE that zone. `null` clears the indicator. */
type DropTarget =
  | { zone: "strip"; at: number }
  | { zone: "overflow"; at: number }
  | null;

export function WorkflowTabs(props: WorkflowTabsProps) {
  const {
    tabs,
    activeSlug,
    statusBySlug,
    onActivate,
    onClose,
    onRename,
    onClone,
    onDelete,
    onReorder,
    onActivateFromOverflow,
  } = props;
  // a tab slug is in this set while it's awaiting Yes/No on its close confirm.
  const [confirmingSlug, setConfirmingSlug] = useState<string | null>(null);
  // right-click menu position + the tab it targets
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; slug: string } | null>(null);
  // inline rename: when set, the tab body becomes an input pre-filled with the slug
  const [renamingSlug, setRenamingSlug] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  // delete confirm: when set, the tab swaps its close X for a Delete? Yes/No row
  const [deletingSlug, setDeletingSlug] = useState<string | null>(null);
  const stripRef = useRef<HTMLDivElement | null>(null);
  const moreBtnRef = useRef<HTMLButtonElement | null>(null);

  // overflow dropdown open state + portal anchor (computed from the More button)
  const [moreOpen, setMoreOpen] = useState(false);
  const [moreAnchor, setMoreAnchor] = useState<{ x: number; y: number } | null>(null);

  // active drag (slug being dragged + its origin zone)
  const draggingRef = useRef<{ slug: string; from: "strip" | "overflow" } | null>(null);
  const [drop, setDrop] = useState<DropTarget>(null);

  const order = useMemo(() => tabs.map((t) => t.slug), [tabs]);
  const tabBySlug = useMemo(() => {
    const m = new Map<string, WorkflowTab>();
    for (const t of tabs) m.set(t.slug, t);
    return m;
  }, [tabs]);
  const { visible, overflow } = useMemo(
    () => computeVisible(order, activeSlug, MAX_VISIBLE),
    [order, activeSlug],
  );

  // close any open confirm when the active tab list shrinks or changes
  useEffect(() => {
    if (confirmingSlug && !tabs.some((t) => t.slug === confirmingSlug)) {
      setConfirmingSlug(null);
    }
  }, [tabs, confirmingSlug]);

  // close the More dropdown when overflow disappears (e.g. a tab got closed
  // and the list now fits inside MAX_VISIBLE).
  useEffect(() => {
    if (moreOpen && overflow.length === 0) setMoreOpen(false);
  }, [moreOpen, overflow.length]);

  // dismiss the More dropdown on Escape (outside-click is wired on the panel).
  useEffect(() => {
    if (!moreOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMoreOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moreOpen]);

  const handleCloseClick = (e: React.MouseEvent, tab: WorkflowTab) => {
    e.stopPropagation();
    const isOn = statusBySlug[tab.slug] === "on";
    if (isOn && tab.dirty) {
      setConfirmingSlug(tab.slug);
      return;
    }
    onClose(tab.slug);
  };

  const handleContext = (e: React.MouseEvent, tab: WorkflowTab) => {
    e.preventDefault();
    e.stopPropagation();
    setCtxMenu({ x: e.clientX, y: e.clientY, slug: tab.slug });
  };

  const startRename = (slug: string) => {
    setRenameDraft(slug);
    setRenamingSlug(slug);
    setCtxMenu(null);
  };
  const commitRename = (slug: string) => {
    const next = renameDraft.trim();
    setRenamingSlug(null);
    if (next && next !== slug) onRename(slug, next);
  };

  const openMore = () => {
    const btn = moreBtnRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    // anchor the dropdown's top-right to the trigger's bottom-right
    setMoreAnchor({ x: r.right, y: r.bottom + 6 });
    setMoreOpen(true);
  };

  // ── drag-and-drop ──────────────────────────────────────────────────────
  // Source: any tab in the strip OR any row in the dropdown.
  // Targets:
  //   - a strip tab (drop before/after based on horizontal midpoint)
  //   - the More button (drop appends to overflow tail)
  //   - a dropdown row (drop before/after based on vertical midpoint)
  //   - the dropdown panel padding below the last row (drop appends to tail)
  // Computed `nextOrder` is committed via onReorder. The drop indicator is a
  // 2px bar (.wf-drop-indicator) inserted between the appropriate siblings.

  const beginDrag = (slug: string, from: "strip" | "overflow") =>
    (e: React.DragEvent) => {
      draggingRef.current = { slug, from };
      try { e.dataTransfer.setData(DRAG_TYPE, slug); } catch { /* ignore */ }
      e.dataTransfer.effectAllowed = "move";
    };

  const endDrag = () => {
    draggingRef.current = null;
    setDrop(null);
  };

  const onStripDragOver = (idx: number) => (e: React.DragEvent) => {
    if (!draggingRef.current) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const after = e.clientX > r.left + r.width / 2;
    setDrop({ zone: "strip", at: idx + (after ? 1 : 0) });
  };

  const onStripContainerDragOver = (e: React.DragEvent) => {
    if (!draggingRef.current) return;
    // only handle empty-strip / tail-drop fallthrough (children handle their own)
    if (e.target !== e.currentTarget) return;
    e.preventDefault();
    setDrop({ zone: "strip", at: visible.length });
  };

  const onMoreBtnDragOver = (e: React.DragEvent) => {
    if (!draggingRef.current) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    // hovering the More trigger appends to the tail of overflow
    setDrop({ zone: "overflow", at: overflow.length });
  };

  const onMoreBtnDrop = (e: React.DragEvent) => {
    if (!draggingRef.current) return;
    e.preventDefault();
    commitDrop({ zone: "overflow", at: overflow.length });
  };

  const onOverflowRowDragOver = (idx: number) => (e: React.DragEvent) => {
    if (!draggingRef.current) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const after = e.clientY > r.top + r.height / 2;
    setDrop({ zone: "overflow", at: idx + (after ? 1 : 0) });
  };

  const onOverflowPanelDragOver = (e: React.DragEvent) => {
    if (!draggingRef.current) return;
    if (e.target !== e.currentTarget) return;
    e.preventDefault();
    setDrop({ zone: "overflow", at: overflow.length });
  };

  const onStripDrop = (e: React.DragEvent) => {
    if (!draggingRef.current || !drop || drop.zone !== "strip") return;
    e.preventDefault();
    commitDrop(drop);
  };

  const onOverflowPanelDrop = (e: React.DragEvent) => {
    if (!draggingRef.current || !drop || drop.zone !== "overflow") return;
    e.preventDefault();
    commitDrop(drop);
  };

  /** Translate (zone, at) within visible/overflow into an absolute index in
   *  `order`, then commit a reordered permutation via onReorder. */
  const commitDrop = (target: DropTarget) => {
    const dragging = draggingRef.current;
    if (!dragging || !target) {
      endDrag();
      return;
    }
    const fromIdx = order.indexOf(dragging.slug);
    if (fromIdx < 0) {
      endDrag();
      return;
    }
    // absolute insertion index in `order`:
    //  - strip target zone: `at` in visible -> same absolute index (visible is
    //    a prefix of order); when at > visible.length, clamp to visible.length.
    //  - overflow target zone: `at` in overflow -> visible.length + at.
    let absInsert: number;
    if (target.zone === "strip") {
      absInsert = Math.min(target.at, visible.length);
    } else {
      absInsert = visible.length + target.at;
    }
    // reorder() expects toIndex AFTER removal; compute that adjustment.
    const toIndex = absInsert > fromIdx ? absInsert - 1 : absInsert;
    const next = reorder(order, fromIdx, toIndex);
    // no-op if the order didn't change
    let changed = false;
    for (let i = 0; i < next.length; i += 1) {
      if (next[i] !== order[i]) { changed = true; break; }
    }
    if (changed) onReorder(next);
    endDrag();
  };

  const renderTab = (slug: string, idxInVisible: number) => {
    const tab = tabBySlug.get(slug);
    if (!tab) return null;
    const active = slug === activeSlug;
    const on = statusBySlug[slug] === "on";
    const confirming = confirmingSlug === slug;
    const deleting = deletingSlug === slug;
    const renaming = renamingSlug === slug;
    // a workflow's identity IS its slug; there is no separate display name.
    const display = slug;
    const dragging = draggingRef.current?.slug === slug;
    return (
      <div
        key={slug}
        role="tab"
        aria-selected={active}
        tabIndex={0}
        draggable={!renaming}
        onDragStart={beginDrag(slug, "strip")}
        onDragEnd={endDrag}
        onDragOver={onStripDragOver(idxInVisible)}
        onDrop={onStripDrop}
        className={`wf-tab${active ? " active" : ""}${(confirming || deleting) ? " confirming" : ""}${dragging ? " dragging" : ""}`}
        title={display}
        onClick={() => !renaming && onActivate(slug)}
        onContextMenu={(e) => handleContext(e, tab)}
        onKeyDown={(e) => {
          if (renaming) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onActivate(slug);
          }
        }}
      >
        <span className={`wf-tab-dot${on ? " on" : ""}`} aria-hidden />
        {renaming ? (
          <input
            className="wf-tab-rename nodrag"
            autoFocus
            value={renameDraft}
            spellCheck={false}
            onChange={(e) => setRenameDraft(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === "Enter") { e.preventDefault(); commitRename(slug); }
              else if (e.key === "Escape") { e.preventDefault(); setRenamingSlug(null); }
            }}
            onBlur={() => commitRename(slug)}
          />
        ) : (
          <span className="wf-tab-name">{display}</span>
        )}

        {confirming ? (
          <span className="wf-tab-confirm" onClick={(e) => e.stopPropagation()}>
            <span className="wf-tab-confirm-label">Close?</span>
            <button type="button" className="wf-tab-confirm-btn danger"
              onClick={(e) => { e.stopPropagation(); setConfirmingSlug(null); onClose(slug); }}
              title="Yes, close this tab">Yes</button>
            <button type="button" className="wf-tab-confirm-btn"
              onClick={(e) => { e.stopPropagation(); setConfirmingSlug(null); }}
              title="Keep open">No</button>
          </span>
        ) : deleting ? (
          <span className="wf-tab-confirm" onClick={(e) => e.stopPropagation()}>
            <span className="wf-tab-confirm-label">Delete?</span>
            <button type="button" className="wf-tab-confirm-btn danger"
              onClick={(e) => { e.stopPropagation(); setDeletingSlug(null); onDelete(slug); }}
              title="Delete this workflow permanently">Yes</button>
            <button type="button" className="wf-tab-confirm-btn"
              onClick={(e) => { e.stopPropagation(); setDeletingSlug(null); }}
              title="Keep">No</button>
          </span>
        ) : renaming ? null : (
          <button
            type="button"
            className="wf-tab-close"
            onClick={(e) => handleCloseClick(e, tab)}
            title="Close tab (turns Off)"
            aria-label={`Close ${display}`}
          >
            <Icon name="close-outline" />
          </button>
        )}
      </div>
    );
  };

  const stripDropIdx = drop && drop.zone === "strip" ? drop.at : -1;

  return (
    <div
      className="wf-tabs"
      ref={stripRef}
      role="tablist"
      aria-label="Open workflows"
      onDragOver={onStripContainerDragOver}
      onDrop={onStripDrop}
    >
      {visible.map((slug, i) => (
        <span key={`slot-${slug}`} className="wf-tab-slot">
          {stripDropIdx === i && <span className="wf-drop-indicator vertical" aria-hidden />}
          {renderTab(slug, i)}
        </span>
      ))}
      {stripDropIdx === visible.length && visible.length > 0 && (
        <span className="wf-drop-indicator vertical" aria-hidden />
      )}

      {overflow.length > 0 && (
        <button
          ref={moreBtnRef}
          type="button"
          className={`wf-tab wf-tabs-more${moreOpen ? " open" : ""}`}
          title={`${overflow.length} more open`}
          onClick={() => (moreOpen ? setMoreOpen(false) : openMore())}
          onDragOver={onMoreBtnDragOver}
          onDrop={onMoreBtnDrop}
        >
          <Icon name="chevron-down-outline" />
          <span className="wf-tab-name">More {overflow.length}</span>
        </button>
      )}

      {ctxMenu && createPortal(
        <ContextMenu
          x={ctxMenu.x}
          y={ctxMenu.y}
          items={[
            { id: "rename", label: "Rename", icon: "create-outline", run: () => startRename(ctxMenu.slug) },
            { id: "clone", label: "Clone", icon: "copy-outline", run: () => { onClone(ctxMenu.slug); setCtxMenu(null); } },
            { id: "delete", label: "Delete", icon: "trash-outline", run: () => { setDeletingSlug(ctxMenu.slug); setCtxMenu(null); } },
          ]}
          onClose={() => setCtxMenu(null)}
        />,
        document.body,
      )}

      {moreOpen && moreAnchor && createPortal(
        <MoreDropdown
          anchor={moreAnchor}
          overflow={overflow}
          tabBySlug={tabBySlug}
          statusBySlug={statusBySlug}
          activeSlug={activeSlug}
          drop={drop && drop.zone === "overflow" ? drop.at : -1}
          onClose={() => setMoreOpen(false)}
          onActivate={(slug) => {
            onActivateFromOverflow(slug);
            setMoreOpen(false);
          }}
          onCloseTab={(slug) => {
            const tab = tabBySlug.get(slug);
            const isOn = statusBySlug[slug] === "on";
            if (tab && isOn && tab.dirty) {
              // promote the same confirm flow as the strip
              setConfirmingSlug(slug);
              setMoreOpen(false);
              return;
            }
            onClose(slug);
          }}
          beginDrag={(slug) => beginDrag(slug, "overflow")}
          endDrag={endDrag}
          onRowDragOver={onOverflowRowDragOver}
          onPanelDragOver={onOverflowPanelDragOver}
          onPanelDrop={onOverflowPanelDrop}
        />,
        document.body,
      )}
    </div>
  );
}

// ── More dropdown (body-portaled) ─────────────────────────────────────────
interface MoreDropdownProps {
  anchor: { x: number; y: number };
  overflow: string[];
  tabBySlug: Map<string, WorkflowTab>;
  statusBySlug: Record<string, Power>;
  activeSlug: string | null;
  /** drop index inside overflow, or -1 if no indicator. */
  drop: number;
  onClose: () => void;
  onActivate: (slug: string) => void;
  onCloseTab: (slug: string) => void;
  beginDrag: (slug: string) => (e: React.DragEvent) => void;
  endDrag: () => void;
  onRowDragOver: (idx: number) => (e: React.DragEvent) => void;
  onPanelDragOver: (e: React.DragEvent) => void;
  onPanelDrop: (e: React.DragEvent) => void;
}

function MoreDropdown(props: MoreDropdownProps) {
  const {
    anchor,
    overflow,
    tabBySlug,
    statusBySlug,
    activeSlug,
    drop,
    onClose,
    onActivate,
    onCloseTab,
    beginDrag,
    endDrag,
    onRowDragOver,
    onPanelDragOver,
    onPanelDrop,
  } = props;
  const panelRef = useRef<HTMLDivElement | null>(null);

  // outside-click closes the panel
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const p = panelRef.current;
      if (!p) return;
      if (e.target instanceof Node && !p.contains(e.target)) onClose();
    };
    // use a microtask delay so the click that opened the panel doesn't dismiss it
    const t = window.setTimeout(() => {
      window.addEventListener("mousedown", onDown);
    }, 0);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("mousedown", onDown);
    };
  }, [onClose]);

  // position: top-right of panel snaps to anchor (top-right of trigger -> below).
  // We render right-aligned by translating left by the panel's measured width;
  // since width is fixed by CSS (~240px) we use that as the offset.
  const style: React.CSSProperties = {
    position: "fixed",
    top: anchor.y,
    left: anchor.x,
    transform: "translateX(-100%)",
  };

  return (
    <div
      ref={panelRef}
      className="wf-tabs-more-panel"
      style={style}
      role="menu"
      onDragOver={onPanelDragOver}
      onDrop={onPanelDrop}
    >
      {overflow.map((slug, i) => {
        const tab = tabBySlug.get(slug);
        if (!tab) return null;
        const on = statusBySlug[slug] === "on";
        const active = slug === activeSlug;
        // a workflow's identity IS its slug; there is no separate display name.
    const display = slug;
        return (
          <span key={slug} className="wf-tabs-more-slot">
            {drop === i && <span className="wf-drop-indicator horizontal" aria-hidden />}
            <div
              role="menuitem"
              tabIndex={0}
              draggable
              onDragStart={beginDrag(slug)}
              onDragEnd={endDrag}
              onDragOver={onRowDragOver(i)}
              className={`wf-tabs-more-row${active ? " active" : ""}`}
              title={display}
              onClick={() => onActivate(slug)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onActivate(slug);
                }
              }}
            >
              <span className={`wf-tab-dot${on ? " on" : ""}`} aria-hidden />
              <span className="wf-tab-name">{display}</span>
              <button
                type="button"
                className="wf-tab-close"
                onClick={(e) => { e.stopPropagation(); onCloseTab(slug); }}
                title="Close tab"
                aria-label={`Close ${display}`}
              >
                <Icon name="close-outline" />
              </button>
            </div>
          </span>
        );
      })}
      {drop === overflow.length && (
        <span className="wf-drop-indicator horizontal" aria-hidden />
      )}
    </div>
  );
}
