// ============================================================================
// SavedWorkflowsPanel: shown in the left rail when libraryMode === "workflows".
// Fetches GET /api/graphs (returns {graphs:[name,...]}) and renders a list of
// saved graph names. Clicking a row opens that workflow as a tab (or activates
// the existing tab). Right-click on a row opens a custom Rename/Clone/Delete
// menu (NOT the browser's native one). A "back to library" link at the top
// flips the rail back.
// ============================================================================
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../lib/icons";
import { ContextMenu } from "./ContextMenu";

interface SavedWorkflowsPanelProps {
  /** Open (or activate) a saved workflow as a tab. */
  onOpenTab: (slug: string) => void;
  /** Create a brand-new empty workflow and open it as a tab. */
  onNewWorkflow: () => void;
  /** Right-click Rename. */
  onRenameWorkflow: (slug: string, next: string) => void;
  /** Right-click Clone: duplicate the saved graph under a fresh slug. */
  onCloneWorkflow: (slug: string) => void;
  /** Right-click Delete: DELETE /api/graphs/{slug} + close any open tab. */
  onDeleteWorkflow: (slug: string) => void;
  /** The slugs currently open as tabs (for a small "open" badge). */
  openSlugs?: string[];
  /** Close (collapse) the WHOLE left rail. The single rail-close button now
   *  lives in this panel's header (replaces the one that was on the Node
   *  Library mid-rail). */
  onCloseRail: () => void;
  /** bumped when a workflow was saved elsewhere (Save, the file menu's Save
   *  as): the list reads the server again. */
  refreshKey?: number;
}

export function SavedWorkflowsPanel({
  onOpenTab, onNewWorkflow, onRenameWorkflow, onCloneWorkflow, onDeleteWorkflow,
  openSlugs = [], onCloseRail, refreshKey = 0,
}: SavedWorkflowsPanelProps) {
  const [names, setNames] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0); // bump to refetch after a rename/delete
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; slug: string } | null>(null);
  const [renamingSlug, setRenamingSlug] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [deletingSlug, setDeletingSlug] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch("/api/graphs")
      .then((r) => r.json())
      .then((d: { graphs: string[] }) => {
        if (!cancelled) setNames(d.graphs ?? []);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e?.message ?? e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [tick, refreshKey]);

  const openSet = new Set(openSlugs);
  const refetch = () => setTick((n) => n + 1);

  return (
    <div className="swf-panel">
      {/* Header: section label + the "+ new workflow" affordance. The rail
          now stacks Workflows on top of NodeLibrary, so the back button is
          gone. */}
      <div className="swf-head">
        <span className="swf-head-lbl">WORKFLOWS</span>
        <button className="rail-close-btn" onClick={onCloseRail} title="Collapse rail">
          <Icon name="close-outline" />
        </button>
      </div>

      <button
        type="button"
        className="swf-new"
        onClick={onNewWorkflow}
        title="Create a new empty workflow and open it as a tab"
      >
        <Icon name="add-outline" />
        <span>New workflow</span>
      </button>

      <div className="swf-scroll">
        {loading && <div className="swf-empty">loading workflows…</div>}
        {error && <div className="swf-empty">unavailable · {error}</div>}
        {!loading && !error && names.length === 0 && (
          <div className="swf-empty">no saved workflows yet</div>
        )}
        {!loading && !error && names.map((name) => {
          const isOpen = openSet.has(name);
          const renaming = renamingSlug === name;
          const deleting = deletingSlug === name;
          return (
            <div
              key={name}
              className={`swf-row${isOpen ? " open" : ""}`}
              onClick={() => !renaming && !deleting && onOpenTab(name)}
              onContextMenu={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setCtxMenu({ x: e.clientX, y: e.clientY, slug: name });
              }}
              title={isOpen ? `Activate "${name}"` : `Open "${name}"`}
            >
              <div className="swf-row-ico">
                <Icon name="git-network-outline" />
              </div>
              <div className="swf-row-text">
                {renaming ? (
                  <input
                    className="wf-tab-rename"
                    autoFocus
                    value={renameDraft}
                    spellCheck={false}
                    onChange={(e) => setRenameDraft(e.target.value)}
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => {
                      e.stopPropagation();
                      if (e.key === "Enter") {
                        e.preventDefault();
                        const next = renameDraft.trim();
                        setRenamingSlug(null);
                        if (next && next !== name) {
                          onRenameWorkflow(name, next);
                          window.setTimeout(refetch, 200);
                        }
                      } else if (e.key === "Escape") {
                        e.preventDefault();
                        setRenamingSlug(null);
                      }
                    }}
                    onBlur={() => setRenamingSlug(null)}
                  />
                ) : (
                  <div className="swf-row-name">{name}</div>
                )}
              </div>
              {deleting ? (
                <span className="wf-tab-confirm" onClick={(e) => e.stopPropagation()}>
                  <span className="wf-tab-confirm-label">Delete?</span>
                  <button type="button" className="wf-tab-confirm-btn danger"
                    onClick={(e) => { e.stopPropagation(); setDeletingSlug(null); onDeleteWorkflow(name); window.setTimeout(refetch, 200); }}>Yes</button>
                  <button type="button" className="wf-tab-confirm-btn"
                    onClick={(e) => { e.stopPropagation(); setDeletingSlug(null); }}>No</button>
                </span>
              ) : (
                <>
                  {isOpen && <span className="swf-row-open">open</span>}
                  <Icon name="chevron-forward-outline" className="swf-row-arrow" />
                </>
              )}
            </div>
          );
        })}
      </div>

      {ctxMenu && createPortal(
        <ContextMenu
          x={ctxMenu.x}
          y={ctxMenu.y}
          items={[
            { id: "rename", label: "Rename", icon: "create-outline", run: () => { setRenameDraft(ctxMenu.slug); setRenamingSlug(ctxMenu.slug); setCtxMenu(null); } },
            { id: "clone", label: "Clone", icon: "copy-outline", run: () => { onCloneWorkflow(ctxMenu.slug); setCtxMenu(null); window.setTimeout(refetch, 200); } },
            { id: "delete", label: "Delete", icon: "trash-outline", run: () => { setDeletingSlug(ctxMenu.slug); setCtxMenu(null); } },
          ]}
          onClose={() => setCtxMenu(null)}
        />,
        document.body,
      )}
    </div>
  );
}
