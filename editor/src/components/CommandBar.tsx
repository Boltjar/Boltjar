// ============================================================================
// CommandBar: the top command-center strip, built around the power model.
// Brand mark, graph breadcrumb (unsaved dot), a live
// power capsule with a breathing indicator when On, the power controls
// (On/Off/Restart) and an ADAPTIVE primary action that follows power + draft:
//   Off + edited  → "Save"            (PUT the graph)
//   Off + clean   → "On"              (validate then start; gated on validity)
//   On  + draft   → "Save & Restart"  (PUT then ws restart)
//   On  + clean   → running           (the primary collapses; Off/Restart remain)
// Plus undo/redo, a live telemetry cluster, and the palette trigger.
// ============================================================================
import { useState } from "react";
import { Icon } from "../lib/icons";
import { mod } from "../lib/platform";
import { WorkflowTabs, type WorkflowTab } from "./WorkflowTabs";
import type { Power } from "../hooks/useRunSocket";

export type PowerPhase = "off" | "on" | "draft" | "invalid";

/** The adaptive primary action the bar surfaces, computed in App. */
export type PrimaryAction = "save" | "on" | "save-restart" | "none";

interface CommandBarProps {
  graphName: string;
  tabs: WorkflowTab[];
  activeSlug: string | null;
  tabsStatus: Record<string, Power>;
  onActivateTab: (slug: string) => void;
  onCloseTab: (slug: string) => void;
  onRenameTab: (slug: string, next: string) => void;
  onCloneTab: (slug: string) => void;
  onDeleteTab: (slug: string) => void;
  onReorderTabs: (nextOrder: string[]) => void;
  onActivateTabFromOverflow: (slug: string) => void;
  dirty: boolean;
  draft: boolean;
  power: "on" | "off";
  connected: boolean;
  phase: PowerPhase;
  primary: PrimaryAction;
  nodeCount: number;
  liveCount: number;
  eventsPerSec: number;
  problemCount: number;
  canUndo: boolean;
  canRedo: boolean;
  saving: boolean;
  /** Receives `true` when the library rail is open. Drives nothing visual
   *  beyond an unobtrusive `data-rail-open` hook; the brand never recolors
   *  on click anymore (it only opens the rail when closed). */
  libraryRailOpen: boolean;
  onPrimary: () => void;
  onOff: () => void;
  onRestart: () => void;
  onUndo: () => void;
  onRedo: () => void;
  onOpenPalette: () => void;
  onShowProblems: () => void;
  onOpenConnections: () => void;
  onBrandClick: () => void;
}

const PHASE_LABEL: Record<PowerPhase, string> = {
  on: "LIVE",
  off: "OFF",
  draft: "DRAFT",
  invalid: "INVALID",
};

const PRIMARY_META: Record<Exclude<PrimaryAction, "none">, { label: string; icon: string; cls: string }> = {
  save: { label: "Save", icon: "save-outline", cls: "save" },
  on: { label: "On", icon: "power", cls: "on" },
  "save-restart": { label: "Save & Restart", icon: "refresh-outline", cls: "restart" },
};

export function CommandBar(props: CommandBarProps) {
  const {
    graphName,
    tabs,
    activeSlug,
    tabsStatus,
    onActivateTab,
    onCloseTab,
    onRenameTab,
    onCloneTab,
    onDeleteTab,
    onReorderTabs,
    onActivateTabFromOverflow,
    dirty,
    power,
    phase,
    primary,
    nodeCount,
    liveCount,
    eventsPerSec,
    problemCount,
    connected,
    canUndo,
    canRedo,
    saving,
    libraryRailOpen,
    onPrimary,
    onOff,
    onRestart,
    onUndo,
    onRedo,
    onOpenPalette,
    onShowProblems,
    onOpenConnections,
    onBrandClick,
  } = props;

  const pm = primary !== "none" ? PRIMARY_META[primary] : null;
  // graphName is now rendered inside the WorkflowTabs strip (the active tab),
  // so the bar itself no longer prints it; keep the prop for API stability.
  void graphName;
  // also unused now: telemetry numbers (moved to canvas overlay) + connected
  // health (had no real action attached). Kept in props for API stability.
  void nodeCount; void liveCount; void eventsPerSec; void connected;

  // inline confirm for Off (Off has a real side effect: it stops the runtime).
  const [confirmOff, setConfirmOff] = useState(false);
  const handleOffClick = () => {
    if (power === "off") return;
    if (confirmOff) {
      setConfirmOff(false);
      onOff();
    } else {
      setConfirmOff(true);
      window.setTimeout(() => setConfirmOff(false), 3000);
    }
  };

  return (
    <header className="cmdbar">
      <button
        className="brand brand-btn"
        data-rail-open={libraryRailOpen}
        onClick={onBrandClick}
        title={libraryRailOpen ? "BOLTJAR" : "Open library"}
      >
        <span className="dia">◇</span>
        BOLTJAR
      </button>
      {/* workflow tabs sit inside the bar, right after the brand: each open
          workflow as a browser-like tab, the active one brighter. There is no
          '+' here by design; new tabs come from the Saved Workflows panel
          (opened via the brand). Per-tab status dot + close X. */}
      <WorkflowTabs
        tabs={tabs}
        activeSlug={activeSlug}
        statusBySlug={tabsStatus}
        onActivate={onActivateTab}
        onClose={onCloseTab}
        onRename={onRenameTab}
        onClone={onCloneTab}
        onDelete={onDeleteTab}
        onReorder={onReorderTabs}
        onActivateFromOverflow={onActivateTabFromOverflow}
      />
      {dirty && (
        <span className="dirty bar-dirty" title="unsaved changes on the active tab" />
      )}

      {/* power controls. Layout (right to left):
            [ Save & Restart? ] [ iPhone-style ON/OFF toggle ]
          The toggle replaces the old runstate pill + power-off button +
          standalone Restart icon. Drag (or click) the knob right -> ON
          (validates first), left -> OFF (asks for a 2nd click). When the
          graph has unsaved edits, the "Save & Restart" button shows on
          the LEFT of the toggle; there is no Restart-alone (if nothing
          changed there's nothing to restart, just flip the toggle). */}
      <div className="transport">
        {/* Save / Save & Restart button moved to the canvas top center
            (rendered by Canvas.tsx); this slot only carries the toggle now. */}
        <button
          type="button"
          className={`power-toggle power-${phase}${confirmOff ? " confirm" : ""}`}
          onClick={
            phase === "invalid"
              ? onShowProblems
              : power === "on"
                ? handleOffClick
                : onPrimary
          }
          title={
            phase === "invalid"
              ? "graph has problems: click to view"
              : power === "on"
                ? (confirmOff ? "Click again to confirm: stop the graph" : "Click to turn OFF")
                : "Click to turn ON (validates first)"
          }
          aria-pressed={power === "on"}
        >
          <span className="pt-label off">{confirmOff ? "CONFIRM?" : "OFF"}</span>
          <span className="pt-label on">{phase === "invalid" ? "INVALID" : "ON"}</span>
          <span className="pt-knob" />
        </button>
        <div className="mini-divider" />
        <button className="icon-btn" title={`Undo (${mod("Z")})`} onClick={onUndo} disabled={!canUndo}>
          <Icon name="arrow-undo-outline" />
        </button>
        <button className="icon-btn" title={`Redo (${mod("Y")})`} onClick={onRedo} disabled={!canRedo}>
          <Icon name="arrow-redo-outline" />
        </button>
      </div>

      {problemCount > 0 && (
        <button className="ti problems" onClick={onShowProblems} title="show problems">
          <Icon name="warning-outline" />
          <b>{problemCount}</b>
        </button>
      )}

      <div className="bar-tools">
        <button className="icon-btn" title={`Command palette (${mod("K")})`} onClick={onOpenPalette}>
          <Icon name="search-outline" />
        </button>
        <button className="icon-btn" title="Connections &amp; settings" onClick={onOpenConnections}>
          <Icon name="settings-outline" />
        </button>
        {/* user avatar removed until there is a real user/account system to back it. */}
      </div>
    </header>
  );
}
