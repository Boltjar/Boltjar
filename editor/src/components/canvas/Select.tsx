// ============================================================================
// Select: THE shared dropdown. One component, one look, a fully styled DARK
// popup (a native <select> can't theme its option list, which is why this
// exists). Every dropdown in the app routes through here: the Knob select, the
// OPERATION knob, the store table/key picker. Change it once, it changes
// everywhere. The popup is portaled to <body> and positioned under the trigger
// so a node's overflow:hidden never clips it. Placeholder is the trigger's empty
// label, NOT a selectable row: a value the options lack still shows as itself
// (hiding a saved value behind the placeholder reads as if it were gone). An
// optional "＋ new…" action sits at the bottom.
// ============================================================================
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../../lib/icons";

export interface SelectOption { value: string; label: string }

interface SelectProps {
  value: string;
  options: ReadonlyArray<SelectOption | string>;
  placeholder?: string;
  onChange: (value: string) => void;
  /** when given, a "＋ <newLabel>" row appears at the bottom and calls this. */
  onNew?: () => void;
  newLabel?: string;
  /** the value is known to be absent from a fully read list: the trigger still
   *  shows it, with a quiet "not found" mark. */
  missing?: boolean;
  className?: string;
}

export function Select({ value, options, placeholder, onChange, onNew, newLabel = "new…", missing = false, className }: SelectProps) {
  const opts: SelectOption[] = options.map((o) => (typeof o === "string" ? { value: o, label: o } : o));
  const current = opts.find((o) => o.value === value);
  const shown = current ? current.label : value;
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<{ left: number; top: number; width: number } | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const r = triggerRef.current?.getBoundingClientRect();
    if (r) setRect({ left: r.left, top: r.bottom + 4, width: r.width });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      // the popup is portaled to <body>, so it is NOT inside the trigger: check
      // both, or a click on an option closes the popup before its onClick fires.
      if (!triggerRef.current?.contains(t) && !popRef.current?.contains(t)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    // defer so the opening click doesn't immediately close it
    const id = window.setTimeout(() => window.addEventListener("mousedown", onDoc), 0);
    window.addEventListener("keydown", onKey);
    return () => { window.clearTimeout(id); window.removeEventListener("mousedown", onDoc); window.removeEventListener("keydown", onKey); };
  }, [open]);

  return (
    <span className={`wf-select ${className ?? ""}`}>
      <button
        type="button"
        ref={triggerRef}
        className={`wf-select-trigger nodrag ${shown ? "" : "placeholder"} ${missing ? "missing" : ""}`}
        title={missing ? `${value} is not in the list` : undefined}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="wf-select-val">{shown || placeholder || "pick…"}</span>
        {missing && <span className="wf-select-note">not found</span>}
        <Icon name="chevron-expand-outline" />
      </button>
      {open && rect && createPortal(
        <div
          ref={popRef}
          className="wf-select-pop nowheel"
          style={{ position: "fixed", left: rect.left, top: rect.top, minWidth: rect.width }}
        >
          {opts.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`wf-select-opt ${o.value === value ? "active" : ""}`}
              onClick={() => { onChange(o.value); setOpen(false); }}
            >
              {o.label}
            </button>
          ))}
          {onNew && (
            <button type="button" className="wf-select-opt wf-select-new" onClick={() => { onNew(); setOpen(false); }}>
              <Icon name="add-outline" /> {newLabel}
            </button>
          )}
        </div>,
        document.body,
      )}
    </span>
  );
}
