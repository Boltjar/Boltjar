// ============================================================================
// Toast: the shared card the editor uses to tell or ask something in passing
// (lib/toasts holds the stack and its rules). A toast with buttons asks a
// question and stays until it is answered or hidden; a plain one hides itself
// after a few seconds, paused while the pointer is on it.
//
// Where it sits: the bottom right of the canvas, right-aligned with the
// minimap and stacked just above it and the zoom cluster, so it never covers
// the minimap, the zoom buttons or the status bar. With no canvas on screen it
// sits just above the status bar (or above the open console). Measured from
// the elements themselves, again whenever one of them changes size.
//
// Look: a tinted card with a full border in its level's colour (never a
// single-side stripe), the level icon, the message wrapping across the card,
// and the buttons in a row (the first primary, the rest quiet, the shared
// confirm buttons). Enters with a 6 px rise and fade (160 ms), still for
// reduced motion. Esc on a focused toast hides it.
// ============================================================================
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { Icon } from "../lib/icons";
import { LEVEL_ICON } from "../lib/consoleFeed";
import { PLAIN_TOAST_MS, shownToasts, toasts, type Toast as ToastData } from "../lib/toasts";

/** Space between the stack and what it sits above, and the side inset. */
const GAP = 12;
const INSET = 16;

interface Anchor {
  right: number;
  bottom: number;
}

/** Where the stack's bottom-right corner goes, in viewport pixels from the
 *  right and bottom edges. */
function measureAnchor(): Anchor {
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  const status = document.querySelector<HTMLElement>(".statusbar");
  const floor = status ? status.getBoundingClientRect().top : vh;
  const canvas = document.querySelector<HTMLElement>("main.canvas");
  const canvasRect = canvas?.getBoundingClientRect();
  const right = canvasRect && canvasRect.width > 0 ? vw - canvasRect.right + INSET : INSET;
  // the minimap and the zoom cluster in the canvas's bottom-right corner: the
  // stack sits above the higher of the two
  let top = floor - INSET + GAP;
  for (const sel of [".react-flow__minimap", ".zoom-ctrl"]) {
    const el = canvas?.querySelector<HTMLElement>(sel);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    if (r.height > 0) top = Math.min(top, r.top);
  }
  return { right: Math.max(INSET, right), bottom: Math.max(INSET, vh - top + GAP) };
}

/** `key` changes when the cards on screen change: a card can arrive just as
 *  the canvas mounts its minimap, so each new set is measured afresh. */
function useAnchor(active: boolean, key: string): Anchor {
  const [anchor, setAnchor] = useState<Anchor>({ right: INSET, bottom: 40 });
  useLayoutEffect(() => {
    if (!active) return;
    const update = () => {
      const next = measureAnchor();
      setAnchor((a) => (a.right === next.right && a.bottom === next.bottom ? a : next));
    };
    update();
    window.addEventListener("resize", update);
    // the rails open and close, the console opens: anything that moves the
    // canvas or the status bar moves the stack with it
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    const watched = [".statusbar", "main.canvas", ".app"]
      .map((s) => document.querySelector<HTMLElement>(s))
      .filter((el): el is HTMLElement => !!el);
    for (const el of watched) ro?.observe(el);
    return () => {
      window.removeEventListener("resize", update);
      ro?.disconnect();
    };
  }, [active, key]);
  return anchor;
}

/** The editor's toast stack (one, in App). */
export function Toaster() {
  const state = useSyncExternalStore(toasts.subscribe, toasts.get);
  const shown = shownToasts(state);
  const anchor = useAnchor(shown.length > 0, shown.map((t) => t.id).join(","));
  if (shown.length === 0) return null;
  return (
    <div className="toasts" style={{ right: anchor.right, bottom: anchor.bottom }}>
      {shown.map((t) => <Toast key={t.id} toast={t} onHide={() => toasts.hide(t.id)} />)}
    </div>
  );
}

/** One card. Plain cards hide themselves after PLAIN_TOAST_MS, the clock
 *  paused while the pointer or the keyboard focus is on them. */
export function Toast({ toast, onHide }: { toast: ToastData; onHide: () => void }) {
  const asks = !!toast.actions && toast.actions.length > 0;
  const msgId = `toast-msg-${toast.id}`;
  const [held, setHeld] = useState(false);
  const left = useRef(PLAIN_TOAST_MS);
  const hideRef = useRef(onHide);
  hideRef.current = onHide;

  useEffect(() => {
    if (asks || held) return;
    const started = Date.now();
    const t = window.setTimeout(() => hideRef.current(), left.current);
    return () => {
      window.clearTimeout(t);
      left.current = Math.max(0, left.current - (Date.now() - started));
    };
  }, [asks, held]);

  return (
    <div
      className={`toast ${toast.level}`}
      role={asks ? "alertdialog" : "status"}
      aria-labelledby={msgId}
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
      onFocus={() => setHeld(true)}
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setHeld(false); }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onHide();
        }
      }}
    >
      <span className="toast-icon" aria-hidden="true">
        <Icon name={LEVEL_ICON[toast.level]} />
      </span>
      <div className="toast-msg" id={msgId}>{toast.message}</div>
      <button type="button" className="toast-close" title="Hide" aria-label="Hide" onClick={onHide}>
        <Icon name="close-outline" />
      </button>
      {asks && (
        <div className="confirm-actions toast-actions">
          {toast.actions!.map((a, i) => (
            <button
              key={a.label}
              type="button"
              className={i === 0 ? "confirm-ok" : "confirm-cancel"}
              onClick={a.run}
            >
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
