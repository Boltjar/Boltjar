// ============================================================================
// TemplateField: the Template node's editor with {tag} autocomplete (the
// headline intelligence). It is a textarea that, as soon as you type `{`, opens
// a popover listing the nodes currently connected to this Template (its tags),
// filtered by what you type after the brace. Picking one inserts `{name}`, which
// materialises the matching named input port. Used inline on the node body and
// in the inspector.
// ============================================================================
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../lib/icons";
import { useAutoGrow } from "../lib/useAutoGrow";

interface TemplateFieldProps {
  value: string;
  /** candidate tag names: the source nodes wired into this Template + live tags. */
  suggestions: string[];
  onChange: (value: string) => void;
  rows?: number;
  /** compact styling for the node body vs the inspector. */
  variant?: "inline" | "inspector";
  /** the node was given a height (a resized node): fill it instead of growing to content. */
  fill?: boolean;
  placeholder?: string;
}

interface AutoState {
  /** index in the text where the open-brace sits. */
  brace: number;
  /** the partial tag already typed after `{`. */
  query: string;
}

export function TemplateField({
  value,
  suggestions,
  onChange,
  rows = 4,
  variant = "inspector",
  fill = false,
  placeholder,
}: TemplateFieldProps) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const [auto, setAuto] = useState<AutoState | null>(null);
  const [active, setActive] = useState(0);
  // the popover is portaled to <body> so a resizable node's overflow:hidden can
  // never clip it; this is its fixed position, taken from the textarea.
  const [acRect, setAcRect] = useState<{ left: number; top: number; width: number } | null>(null);

  // Local draft buffer. The textarea is driven by `draft` (updated synchronously
  // on each keystroke, so the caret never moves) and we propagate up via onChange.
  // The round-trip through graph state would otherwise re-assign the textarea's
  // value a tick later and snap the caret to the end on every character. We only
  // re-seed from the prop on a genuine external change (undo, a tag insertion, a
  // programmatic edit), detected by comparing against the last value we emitted.
  const [draft, setDraft] = useState(value);
  const lastEmit = useRef(value);
  useEffect(() => {
    if (value !== lastEmit.current) {
      lastEmit.current = value;
      setDraft(value);
    }
  }, [value]);
  const emit = (next: string) => {
    lastEmit.current = next;
    setDraft(next);
    onChange(next);
  };

  // detect an open `{…` token immediately left of the caret with no closing `}`.
  const detect = (text: string, caret: number) => {
    const upto = text.slice(0, caret);
    const brace = upto.lastIndexOf("{");
    if (brace < 0) return setAuto(null);
    const between = upto.slice(brace + 1);
    if (between.includes("}") || /\s/.test(between)) return setAuto(null);
    setAuto({ brace, query: between });
  };

  const filtered = auto
    ? suggestions.filter((s) => s.toLowerCase().includes(auto.query.toLowerCase()))
    : [];

  // position the portaled popover under the textarea whenever it opens.
  useLayoutEffect(() => {
    if (!auto) { setAcRect(null); return; }
    const r = taRef.current?.getBoundingClientRect();
    if (r) setAcRect({ left: r.left, top: r.bottom + 2, width: r.width });
  }, [auto]);

  // keep the active index valid as the candidate list changes.
  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  // reset the highlight to the top match only when the query actually changes,
  // not on every keystroke, so Arrow keys can walk the whole list without it
  // snapping back to the first row.
  useEffect(() => {
    setActive(0);
  }, [auto?.query]);

  const accept = (tag: string) => {
    if (!auto) return;
    const el = taRef.current;
    const caret = el ? el.selectionStart : draft.length;
    const before = draft.slice(0, auto.brace);
    const after = draft.slice(caret);
    const insert = `{${tag}}`;
    const next = before + insert + after;
    emit(next);
    setAuto(null);
    // restore the caret just after the inserted tag.
    const pos = before.length + insert.length;
    requestAnimationFrame(() => {
      if (el) {
        el.focus();
        el.setSelectionRange(pos, pos);
      }
    });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    e.stopPropagation();
    if (auto && filtered.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((a) => Math.min(filtered.length - 1, a + 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((a) => Math.max(0, a - 1));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        accept(filtered[active]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setAuto(null);
        return;
      }
    }
  };

  // auto-grow the inline textarea to its content (lib/useAutoGrow), unless it
  // fills a resized node's height.
  useAutoGrow(taRef, variant === "inline" && !fill, draft);

  return (
    <div className={`tplfield ${variant}`}>
      <textarea
        ref={taRef}
        className="nodrag nowheel"
        value={draft}
        rows={rows}
        spellCheck={false}
        placeholder={placeholder}
        onChange={(e) => {
          emit(e.target.value);
          detect(e.target.value, e.target.selectionStart);
        }}
        onClick={(e) => detect(draft, (e.target as HTMLTextAreaElement).selectionStart)}
        onKeyUp={(e) => detect(draft, (e.target as HTMLTextAreaElement).selectionStart)}
        onKeyDown={onKeyDown}
        onBlur={() => window.setTimeout(() => setAuto(null), 120)}
      />
      {auto && acRect && createPortal(
        <div
          className="tpl-ac nodrag nowheel"
          style={{ position: "fixed", left: acRect.left, top: acRect.top, right: "auto", minWidth: acRect.width }}
        >
          <div className="tpl-ac-head">
            <Icon name="bulb-outline" /> tags {auto.query && <span className="tpl-ac-q">·{auto.query}</span>}
          </div>
          {filtered.length === 0 ? (
            <div className="tpl-ac-empty">
              {suggestions.length === 0 ? "wire a node into this Template" : "no matching tag"}
            </div>
          ) : (
            filtered.map((s, i) => (
              <button
                key={s}
                className={`tpl-ac-row ${i === active ? "active" : ""}`}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  accept(s);
                }}
              >
                <span className="tpl-ac-brace">{"{"}</span>
                <span className="tpl-ac-name">{s}</span>
                <span className="tpl-ac-brace">{"}"}</span>
              </button>
            ))
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
