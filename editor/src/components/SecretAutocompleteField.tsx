// ============================================================================
// SecretAutocompleteField: a text / multiline input with two autocomplete modes:
//   • `{{` opens the SECRETS picker (insert `{{secret.NAME}}`)
//   • `{`  opens the TAGS picker (insert `{NAME}`) when tagSuggestions is given
// Used by the HTTP node's url / headers / query / body fields so the user can
// drop both a secret reference and a wired-tag pipe into the same field. The
// popover reuses the .tpl-ac classes so it looks identical to the Template
// node's {tag} autocomplete.
// ============================================================================
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../lib/icons";
import { useSecrets } from "../hooks/useSecrets";
import { useAutoGrow } from "../lib/useAutoGrow";

interface SecretAutocompleteFieldProps {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  multiline?: boolean;
  rows?: number;
  /** when true, the inline textarea auto-grows to its content (caps at 320px). */
  inline?: boolean;
  /** auto-grow the inline textarea to its content. Off for an `expand` field on a
   *  resized node, whose height the node's flex layout drives (so the two don't fight). */
  autoGrow?: boolean;
  /** when set, a single `{` opens a TAGS popover with these suggestions (the
   *  source nodes wired into a Template-shaped node, like the HTTP `tag` port). */
  tagSuggestions?: string[];
}

type AutoMode = "secret" | "tag";
interface AutoState { brace: number; query: string; mode: AutoMode; }

export function SecretAutocompleteField({
  value, onChange, placeholder, multiline = false, rows = 1, inline = true,
  autoGrow = true, tagSuggestions,
}: SecretAutocompleteFieldProps) {
  const { names } = useSecrets();
  const [draft, setDraft] = useState(value);
  const [auto, setAuto] = useState<AutoState | null>(null);
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLTextAreaElement | HTMLInputElement>(null);
  // popover is portaled to <body> so a resizable node's overflow:hidden never
  // clips it; this fixed position is taken from the field.
  const [acRect, setAcRect] = useState<{ left: number; top: number; width: number } | null>(null);
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

  // Walk left from the caret to the last `{`. If that brace is immediately
  // preceded by another `{`, the user is inside `{{...` and we open the SECRETS
  // popover (existing behaviour). Otherwise it is a single `{...` token and we
  // open the TAGS popover (only when tagSuggestions is given). Whitespace inside
  // the token closes the popover, matching the Template field grammar.
  const detect = (text: string, caret: number) => {
    const upto = text.slice(0, caret);
    const brace = upto.lastIndexOf("{");
    if (brace < 0) return setAuto(null);
    const isDouble = brace > 0 && upto[brace - 1] === "{";
    if (isDouble) {
      // `{{...` token: the open is at brace-1; the query is everything after `{{`.
      const open = brace - 1;
      const between = upto.slice(open + 2);
      if (between.includes("}}") || /\s/.test(between)) return setAuto(null);
      const query = between.startsWith("secret.") ? between.slice("secret.".length) : between;
      return setAuto({ brace: open, query, mode: "secret" });
    }
    // `{...` token (single brace). Only when the field carries tag suggestions
    // do we open the tags popover; otherwise nothing pops (avoid noise).
    if (!tagSuggestions) return setAuto(null);
    const between = upto.slice(brace + 1);
    if (between.includes("}") || /\s/.test(between)) return setAuto(null);
    return setAuto({ brace, query: between, mode: "tag" });
  };

  const candidates = !auto
    ? []
    : auto.mode === "secret"
      ? names
      : (tagSuggestions ?? []);
  const filtered = auto
    ? candidates.filter((n) => n.toLowerCase().includes(auto.query.toLowerCase()))
    : [];

  // position the portaled popover under the field whenever it opens.
  useLayoutEffect(() => {
    if (!auto) { setAcRect(null); return; }
    const r = ref.current?.getBoundingClientRect();
    if (r) setAcRect({ left: r.left, top: r.bottom + 2, width: r.width });
  }, [auto]);

  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  useEffect(() => {
    setActive(0);
  }, [auto?.query, auto?.mode]);

  const accept = (name: string) => {
    if (!auto) return;
    const el = ref.current;
    const caret = el ? (el as HTMLTextAreaElement).selectionStart ?? draft.length : draft.length;
    const before = draft.slice(0, auto.brace);
    const after = draft.slice(caret);
    const insert = auto.mode === "secret" ? `{{secret.${name}}}` : `{${name}}`;
    const next = before + insert + after;
    emit(next);
    setAuto(null);
    const pos = before.length + insert.length;
    requestAnimationFrame(() => {
      if (el) {
        el.focus();
        (el as HTMLTextAreaElement).setSelectionRange(pos, pos);
      }
    });
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
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

  // grow to content (lib/useAutoGrow), re-fit when a resize re-wraps the text.
  useAutoGrow(ref, multiline && inline && autoGrow, draft);

  const onChangeEvt = (e: React.ChangeEvent<HTMLTextAreaElement | HTMLInputElement>) => {
    emit(e.target.value);
    detect(e.target.value, (e.target as HTMLTextAreaElement).selectionStart ?? e.target.value.length);
  };

  const onCaretMove = (e: React.SyntheticEvent) => {
    const t = e.target as HTMLTextAreaElement;
    detect(draft, t.selectionStart ?? draft.length);
  };

  return (
    <div className="secret-ac">
      {multiline ? (
        <textarea
          ref={ref as React.RefObject<HTMLTextAreaElement>}
          className="nodrag nowheel"
          value={draft}
          rows={rows}
          spellCheck={false}
          placeholder={placeholder}
          onChange={onChangeEvt}
          onClick={onCaretMove}
          onKeyUp={onCaretMove}
          onKeyDown={onKeyDown}
          onBlur={() => window.setTimeout(() => setAuto(null), 120)}
        />
      ) : (
        <input
          ref={ref as React.RefObject<HTMLInputElement>}
          className="nodrag"
          type="text"
          value={draft}
          spellCheck={false}
          placeholder={placeholder}
          onChange={onChangeEvt}
          onClick={onCaretMove}
          onKeyUp={onCaretMove}
          onKeyDown={onKeyDown}
          onBlur={() => window.setTimeout(() => setAuto(null), 120)}
        />
      )}
      {auto && acRect && createPortal(
        // nodrag+nowheel are required: this popover sits inside a React Flow
        // node, so without them RF treats clicks as a pan-start and the row
        // click never reaches its handler. Portaled to <body> + fixed-positioned
        // so a resizable node's overflow:hidden never clips it.
        <div
          className="tpl-ac nodrag nowheel"
          style={{ position: "fixed", left: acRect.left, top: acRect.top, right: "auto", minWidth: acRect.width }}
        >
          <div className="tpl-ac-head">
            {auto.mode === "secret" ? (
              <>
                <Icon name="lock-closed-outline" /> secrets
                <span className="tpl-ac-q"> · {auto.query || "(any)"}</span>
              </>
            ) : (
              <>
                <Icon name="bulb-outline" /> tags
                {auto.query && <span className="tpl-ac-q">·{auto.query}</span>}
              </>
            )}
          </div>
          {filtered.length === 0 ? (
            <div className="tpl-ac-empty">
              {auto.mode === "secret"
                ? (names.length === 0
                    ? "no secrets yet: add one in Settings, Secrets"
                    : `no secret matches "${auto.query}"`)
                : (candidates.length === 0
                    ? "wire a node into the tag port"
                    : "no matching tag")}
            </div>
          ) : (
            filtered.map((name, i) => (
              <button
                key={name}
                type="button"
                className={`tpl-ac-row ${i === active ? "active" : ""}`}
                onMouseDown={(e) => { e.preventDefault(); accept(name); }}
              >
                {auto.mode === "secret" ? (
                  <>
                    <span className="tpl-ac-brace">{"{{"}</span>
                    <span className="tpl-ac-prefix">secret.</span>
                    <span className="tpl-ac-name">{name}</span>
                    <span className="tpl-ac-brace">{"}}"}</span>
                  </>
                ) : (
                  <>
                    <span className="tpl-ac-brace">{"{"}</span>
                    <span className="tpl-ac-name">{name}</span>
                    <span className="tpl-ac-brace">{"}"}</span>
                  </>
                )}
              </button>
            ))
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
