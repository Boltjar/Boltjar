// ============================================================================
// Knob: THE single config-knob renderer. One component, one look, one set of
// behaviours, shared by every node's inline knobs (the node body), the LLM's
// model params, and the inspector. A bounded number shows a value + slider, a
// bool a toggle, a select a dropdown, text an input. There is NO inline convert
// icon: "Convert to input" (promote to a typed port) and "Reset to default" live
// on the knob's own RIGHT-CLICK menu, which never leaks the node's menu.
// ============================================================================
import { useState, type CSSProperties, type MouseEvent } from "react";
import { createPortal } from "react-dom";
import { useDraft } from "../../lib/useDraft";
import { declaredOption } from "../../lib/knobOptions";
import { Icon } from "../../lib/icons";
import { ContextMenu, type MenuItem } from "../ContextMenu";
import { Select, type SelectOption } from "./Select";

export type KnobKind = "number" | "bool" | "select" | "text";

export interface KnobProps {
  label: string;
  kind: KnobKind;
  value: unknown;
  default?: unknown;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  options?: unknown[];
  onChange: (v: unknown) => void;
  /** promote this knob to a typed input port (right-click "Convert to input"). */
  onConvert?: () => void;
  /** reset this knob to its declared default (right-click "Reset to default"). */
  onReset?: () => void;
}

export function Knob(props: KnobProps) {
  const { label, kind, value, onChange, onConvert, onReset } = props;
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  // a draft buffer for the text knob so the caret holds while the value round-trips.
  const [textDraft, emitText] = useDraft(String(value ?? props.default ?? ""), onChange as (v: string) => void);

  // a right-click opens THIS knob's menu (Convert / Reset) and must not reach the
  // node's context menu (Rename/Duplicate/...). Stop the React AND native bubble.
  const openMenu = (e: MouseEvent) => {
    if (!onConvert && !onReset) return;
    e.preventDefault();
    e.stopPropagation();
    e.nativeEvent.stopImmediatePropagation();
    setMenu({ x: e.clientX, y: e.clientY });
  };

  const items: MenuItem[] = [];
  if (onConvert) items.push({ id: "convert", label: "Convert to input", icon: "enter-outline", run: onConvert });
  if (onReset) items.push({ id: "reset", label: "Reset to default", icon: "refresh-outline", run: onReset });
  const menuEl = menu
    ? createPortal(<ContextMenu x={menu.x} y={menu.y} items={items} onClose={() => setMenu(null)} />, document.body)
    : null;

  if (kind === "bool") {
    return (
      <div className="knob bool" onContextMenu={openMenu}>
        <button type="button" className={`knob-toggle nodrag ${value ? "on" : ""}`} onClick={() => onChange(!value)}>
          <span className="knob-lbl">{label}</span>
          <span className="kt-track"><span className="kt-knob" /></span>
        </button>
        {menuEl}
      </div>
    );
  }

  if (kind === "select") {
    return (
      <div className="knob select" onContextMenu={openMenu}>
        <span className="knob-lbl">{label}</span>
        <Select
          value={String(value ?? props.default ?? "")}
          // string options render value=label; {value,label} options keep a
          // friendly label (e.g. "new line") distinct from the value ("\n").
          options={(props.options ?? []).map((o) =>
            o && typeof o === "object" ? (o as SelectOption) : String(o),
          )}
          // the Select shows text; store the DECLARED option (an int stays an int).
          onChange={(text) => onChange(declaredOption(props.options ?? [], text))}
          className="knob-select"
        />
        {menuEl}
      </div>
    );
  }

  if (kind === "text") {
    return (
      <div className="knob text" onContextMenu={openMenu}>
        <span className="knob-lbl">{label}</span>
        <input
          className="nodrag"
          type="text"
          value={textDraft}
          spellCheck={false}
          onChange={(e) => emitText(e.target.value)}
          onKeyDown={(e) => e.stopPropagation()}
        />
        {menuEl}
      </div>
    );
  }

  // number (+ a slider when min+max bound it), falling back to the default so it
  // is never blank.
  const num = value === undefined || value === null ? Number(props.default ?? 0) : Number(value);
  const min = props.min ?? null;
  const max = props.max ?? null;
  const bounded = min !== null && max !== null;
  const step = props.step ?? 0.01;
  const frac = bounded && max !== min ? Math.max(0, Math.min(1, (num - min) / (max - min))) : 0;
  return (
    <div className="knob num" onContextMenu={openMenu}>
      <div className="knob-numrow">
        <span className="knob-lbl">{label}</span>
        <input
          className="nodrag"
          type="number"
          value={Number.isFinite(num) ? num : ""}
          min={min ?? undefined}
          max={max ?? undefined}
          step={step}
          onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
          onKeyDown={(e) => e.stopPropagation()}
        />
      </div>
      {bounded && (
        <input
          className="knob-slider nodrag"
          type="range"
          value={Number.isFinite(num) ? num : (min ?? 0)}
          min={min ?? 0}
          max={max ?? 1}
          step={step}
          style={{ ["--frac" as string]: String(frac) } as CSSProperties}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      )}
      {menuEl}
    </div>
  );
}
