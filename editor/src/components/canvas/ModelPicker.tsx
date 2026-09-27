// ============================================================================
// ModelPicker: a model node's model selector. A button showing the current
// model's label opens a searchable dropdown of the models that can run now,
// grouped by provider, each row a label + capability chips (in/out modalities,
// tools, thinking, the context size). A picker that offers "auto" lists it first:
// the runtime runs the first runnable model, and the row says which one. A
// footer says when the list was last updated and refreshes it on demand.
// Selecting a model calls back so the node can set config.model, reset params to
// the model's defaults and drop stale promotions. It never mutates state itself
// (it only reports a choice), so the graph store stays the single source of truth.
//
// The dropdown is rendered in a PORTAL to document.body and positioned in screen
// space. A node lives inside React Flow's zoomed/transformed canvas, so an
// in-node panel would scale with the zoom (tiny at low zoom) and stack against
// the node's own knobs. A screen-space portal stays full size and above all of
// it, the way a real node editor's popovers behave.
// ============================================================================
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import type { ModelManifest } from "../../types/protocol";
import { Icon } from "../../lib/icons";
import { typeColorVar } from "../../lib/types";
import {
  AUTO_MODEL,
  formatContext,
  modalityIcon,
  modelStatus,
  pickerGroups,
  providerLabel,
  runnableModels,
  updatedLine,
} from "../../lib/modelMeta";
import { useEditor } from "../../lib/editorContext";

interface ModelPickerProps {
  /** every model the server lists; the picker keeps the runnable ones of `kind`. */
  manifests: ModelManifest[];
  /** the family this picker lists (the model widget's model_kind). */
  kind: string;
  /** offer "auto" above the list (declared on the model widget). */
  offersAuto?: boolean;
  selectedId: string;
  /** the resolved manifest for selectedId, if any (so a missing model still shows). */
  selected: ModelManifest | undefined;
  onSelect: (modelId: string) => void;
  variant?: "node" | "inspector";
}

interface TriggerRect {
  left: number;
  top: number;
  bottom: number;
  width: number;
}

const PANEL_MIN_W = 264;
const PANEL_MAX_H = 340;

export function ModelPicker({
  manifests,
  kind,
  offersAuto = false,
  selectedId,
  selected,
  onSelect,
  variant = "node",
}: ModelPickerProps) {
  const { openConnections, models, modelsMeta, reloadModels, refreshModels } = useEditor();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<TriggerRect | null>(null);

  // close on outside-click / Escape. The panel is portaled out of rootRef, so we
  // also treat clicks inside it as "inside". We listen on `click`, not
  // `mousedown`: a canvas pan is a drag (mousedown→move→mouseup with no click),
  // so panning the graph keeps the picker open (it follows, see below) while a
  // genuine click-away still dismisses it.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!rootRef.current?.contains(t) && !panelRef.current?.contains(t)) setOpen(false);
    };
    window.addEventListener("click", onClick);
    return () => window.removeEventListener("click", onClick);
  }, [open]);

  // opening reads the list again: the server answers from its cache and
  // refreshes a stale list in the background.
  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      void reloadModels();
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open, reloadModels]);

  // measure the trigger in screen space and decide up/down (keeps the panel
  // on-screen). The trigger lives on a node inside React Flow's transformed
  // canvas, so panning/zooming moves it in screen space without firing any DOM
  // event we could hook. We re-measure on every animation frame while open and
  // commit only on a real change, so the portaled panel tracks the node instead
  // of detaching.
  useLayoutEffect(() => {
    if (!open) return;
    let raf = 0;
    let prev: TriggerRect | null = null;
    const tick = () => {
      const el = rootRef.current;
      if (el) {
        const r = el.getBoundingClientRect();
        if (
          !prev ||
          Math.abs(prev.left - r.left) > 0.5 ||
          Math.abs(prev.top - r.top) > 0.5 ||
          Math.abs(prev.bottom - r.bottom) > 0.5 ||
          Math.abs(prev.width - r.width) > 0.5
        ) {
          prev = { left: r.left, top: r.top, bottom: r.bottom, width: r.width };
          setRect(prev);
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [open]);

  const runnable = useMemo(() => runnableModels(manifests, kind), [manifests, kind]);
  const groups = useMemo(() => pickerGroups(manifests, kind, query), [manifests, kind, query]);
  const flat = useMemo(() => groups.flatMap((g) => g.models), [groups]);
  const q = query.trim().toLowerCase();
  const showAuto = offersAuto && (!q || "auto".includes(q));
  // keyboard order: auto first (when offered), then the rows as grouped.
  const navIds = useMemo(
    () => [...(showAuto ? [AUTO_MODEL] : []), ...flat.map((m) => m.id)],
    [showAuto, flat],
  );

  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, navIds.length - 1)));
  }, [navIds.length]);

  const pick = (id: string) => {
    onSelect(id);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    e.stopPropagation();
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(navIds.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (navIds[active]) pick(navIds[active]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  };

  const status = modelStatus(selectedId, models, modelsMeta.auto);
  const warn = status.state === "missing" || status.state === "unavailable";
  const label =
    status.state === "auto" ? "Auto" : selected?.label ?? (selectedId || "select a model");
  let sub = "";
  if (status.state === "auto" || warn) sub = status.note;
  else if (selected) {
    // a voice / embed model has no token context: show just its provider.
    sub = `${providerLabel(selected.provider)}${selected.context > 0 ? ` · ${formatContext(selected.context)} ctx` : ""}`;
  }
  const autoNote = modelStatus(AUTO_MODEL, models, modelsMeta.auto).note;

  // screen-space placement for the portaled panel: prefer below the trigger,
  // flip above when it would not fit, and clamp to the viewport otherwise.
  let panelStyle: CSSProperties | undefined;
  let dropClass: "down" | "up" = "down";
  if (rect) {
    const width = Math.max(rect.width, PANEL_MIN_W);
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
    const below = rect.bottom + 5;
    const above = rect.top - PANEL_MAX_H - 5;
    let top: number;
    if (below + PANEL_MAX_H <= window.innerHeight - 8) {
      top = below;
    } else if (above >= 8) {
      top = above;
      dropClass = "up";
    } else {
      top = Math.max(8, window.innerHeight - PANEL_MAX_H - 8);
    }
    // the panel is portaled to <body> and fully positioned here via top/left, so
    // override the legacy .up/.down CSS that sets bottom/top (a leaked `bottom`
    // from .mp-panel.up would push a fixed panel above the viewport = invisible).
    panelStyle = { position: "fixed", left, width, top, right: "auto", bottom: "auto" };
  }

  return (
    <div className={`modelpick ${variant}`} ref={rootRef}>
      <button
        type="button"
        className={`mp-trigger nodrag ${open ? "open" : ""} ${warn || status.state === "none" ? "missing" : ""}`}
        onClick={() => setOpen((o) => !o)}
        title={warn || status.state === "auto" ? status.note : selected ? selected.summary : "no model selected"}
      >
        <span className="mp-trig-ico">
          <Icon name={warn ? "warning-outline" : "sparkles"} />
        </span>
        <span className="mp-trig-text">
          <span className="mp-trig-label">{label}</span>
          {sub && <span className={`mp-trig-sub ${warn ? "warn" : ""}`}>{sub}</span>}
        </span>
        <Icon name="chevron-expand-outline" className="mp-trig-chev" />
      </button>

      {open && panelStyle && createPortal(
        <div className={`mp-panel portal ${dropClass}`} ref={panelRef} style={panelStyle}>
          <div className="mp-search">
            <Icon name="search-outline" />
            <input
              ref={inputRef}
              value={query}
              placeholder="search models…"
              spellCheck={false}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onKeyDown}
            />
          </div>
          <div className="mp-list nowheel">
            {showAuto && (
              <button
                type="button"
                className={`mp-row ${navIds[active] === AUTO_MODEL ? "active" : ""} ${selectedId === AUTO_MODEL ? "selected" : ""}`}
                onMouseEnter={() => setActive(0)}
                onClick={() => pick(AUTO_MODEL)}
              >
                <span className="mp-row-main">
                  <span className="mp-row-label">Auto</span>
                  {selectedId === AUTO_MODEL && <Icon name="checkmark" className="mp-row-check" />}
                </span>
                <span className="mp-row-note">{autoNote}</span>
              </button>
            )}
            {flat.length === 0 ? (
              runnable.length === 0 ? (
                // Nothing of this family can run: invite the user to add a connection.
                <button
                  type="button"
                  className="mp-getmore nodrag"
                  onClick={() => { setOpen(false); openConnections(); }}
                >
                  <span className="mp-getmore-tile">
                    <Icon name="git-network-outline" />
                  </span>
                  <span className="mp-getmore-text">
                    <span className="mp-getmore-primary">Add a connection</span>
                    <span className="mp-getmore-secondary">connect a provider to get models</span>
                  </span>
                  <Icon name="chevron-forward-outline" className="mp-getmore-arrow" />
                </button>
              ) : showAuto ? null : (
                <div className="mp-empty">no matching model</div>
              )
            ) : (
              groups.map((grp) => (
                <div className="mp-group" key={grp.provider}>
                  <div className="mp-group-head">{providerLabel(grp.provider)}</div>
                  {grp.models.map((m) => {
                    const idx = navIds.indexOf(m.id);
                    return (
                      <button
                        type="button"
                        key={m.id}
                        className={`mp-row ${idx === active ? "active" : ""} ${m.id === selectedId ? "selected" : ""}`}
                        onMouseEnter={() => setActive(idx)}
                        onClick={() => pick(m.id)}
                        title={m.summary}
                      >
                        <span className="mp-row-main">
                          <span className="mp-row-label">{m.label}</span>
                          {m.id === selectedId && <Icon name="checkmark" className="mp-row-check" />}
                        </span>
                        <ModelChips manifest={m} />
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>
          <div className="mp-foot">
            <span className="mp-updated">{updatedLine(modelsMeta, Date.now())}</span>
            <button
              type="button"
              className={`mp-refresh nodrag ${modelsMeta.refreshing ? "busy" : ""}`}
              disabled={modelsMeta.refreshing}
              onClick={() => void refreshModels()}
              title="Ask every connected provider for its models now"
            >
              <Icon name="refresh-outline" /> Refresh
            </button>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

/** The small capability chips on a model row: in→out modalities, tools,
 *  thinking, context. */
export function ModelChips({ manifest, compact = false }: { manifest: ModelManifest; compact?: boolean }) {
  return (
    <span className={`mp-chips ${compact ? "compact" : ""}`}>
      <span className="mp-modgroup">
        {manifest.inputs.map((m) => (
          <span
            key={`in-${m}`}
            className="mp-chip mod"
            style={{ ["--pc" as string]: typeColorVar(m === "text" ? "text" : m) }}
            title={`input: ${m}`}
          >
            <Icon name={modalityIcon(m)} />
          </span>
        ))}
      </span>
      <span className="mp-arrow">→</span>
      <span className="mp-modgroup">
        {manifest.outputs.map((m) => (
          <span
            key={`out-${m}`}
            className="mp-chip mod"
            style={{ ["--pc" as string]: typeColorVar(m === "text" ? "text" : m) }}
            title={`output: ${m}`}
          >
            <Icon name={modalityIcon(m)} />
          </span>
        ))}
      </span>
      {manifest.tools && (
        <span className="mp-chip tools" title="supports tool-calls">
          <Icon name="construct-outline" /> tools
        </span>
      )}
      {manifest.thinking && (
        <span className="mp-chip think" title="can reason before it answers">
          <Icon name="bulb-outline" /> think
        </span>
      )}
      {manifest.context > 0 && (
        <span className="mp-chip ctx" title={`${manifest.context.toLocaleString()} token context`}>
          {formatContext(manifest.context)}
        </span>
      )}
    </span>
  );
}
