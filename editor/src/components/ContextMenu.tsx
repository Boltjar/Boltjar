// ============================================================================
// ContextMenu: the right-click menu (node / canvas / edge), suppressing the
// browser menu. Two flavours over one shell:
//   • a plain list of actions (with icons, shortcut hints, separators, danger);
//   • a node-search submenu (canvas "Add node", edge "Insert node") that filters
//     the catalog and adds at the click point.
// Positions itself within the viewport and closes on outside-click / Escape.
// Opened from a button (the top bar's Help), `align: "end"` hangs the menu
// from the anchor's right edge instead of starting at it.
// The items are a WAI-ARIA menu: focus lands on the first item (or the search
// box) when it opens, ↑/↓/Home/End move it (lib/menuKeys), Tab and Escape
// close it, and focus goes back to where it was before the menu opened.
// ============================================================================
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { NodeDef } from "../types/protocol";
import { Icon, hasIcon } from "../lib/icons";
import { functionColorVar, nodeIcon } from "../lib/kinds";
import { menuFocusTarget } from "../lib/menuKeys";
import { capabilityHint } from "../lib/nodeMeta";

export interface MenuItem {
  id: string;
  label: string;
  icon?: string;
  kbd?: string;
  danger?: boolean;
  disabled?: boolean;
  separatorBefore?: boolean;
  /** a coloured swatch shown in place of the icon (group recolour items). */
  swatch?: string;
  /** marks the current choice with a trailing check (group's active colour). */
  active?: boolean;
  run: () => void;
}

export type ContextMenuKind = "node" | "canvas" | "edge" | "group";

interface ContextMenuProps {
  x: number;
  y: number;
  /** "start" (default): the menu's left edge sits at x, like a right-click menu.
   *  "end": its right edge sits at x, for a menu dropped from a button near the
   *  right side of the screen. */
  align?: "start" | "end";
  /** plain action items (node + edge menus, and the canvas non-search items). */
  items: MenuItem[];
  /** the menu's name for assistive tech ("Help"). */
  label?: string;
  /** when set, render a node-search section titled by `searchTitle`. */
  defs?: NodeDef[];
  searchTitle?: string;
  onPickNode?: (typeId: string) => void;
  onClose: () => void;
}

/** The items focus can land on: every menu item that is not disabled. */
const ITEM_SELECTOR = '[role^="menuitem"]:not(:disabled)';

export function ContextMenu({ x, y, align = "start", items, label, defs, searchTitle, onPickNode, onClose }: ContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  // an end-aligned menu is first laid out at the left edge: at `left: x` (near
  // the right of the screen) it would shrink to the space left and measure narrow.
  const [pos, setPos] = useState({ x: align === "end" ? 0 : x, y });
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  const searchable = !!defs && !!onPickNode;

  // clamp into the viewport once measured.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    let nx = align === "end" ? x - r.width : x;
    let ny = y;
    if (nx + r.width > window.innerWidth - 8) nx = window.innerWidth - r.width - 8;
    if (y + r.height > window.innerHeight - 8) ny = window.innerHeight - r.height - 8;
    setPos({ x: Math.max(8, nx), y: Math.max(8, ny) });
  }, [x, y, align]);

  // focus goes back where it was when the menu opened (the Help button, a
  // node), and only from inside the menu: an item that moved focus on purpose
  // (Rename's field) keeps it. A layout effect, so it runs before anything the
  // item's action mounts can take focus.
  useLayoutEffect(() => {
    const opener = document.activeElement;
    const menu = ref.current;
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected && menu?.contains(document.activeElement)) {
        opener.focus({ preventScroll: true });
      }
    };
  }, []);

  const itemEls = () => Array.from(ref.current?.querySelectorAll<HTMLElement>(ITEM_SELECTOR) ?? []);

  /** Focus stop `i`: an item, or the search box as the stop past the last one. */
  const focusStop = (i: number | null) => {
    if (i === null) return;
    (itemEls()[i] ?? searchRef.current)?.focus({ preventScroll: true });
  };

  useEffect(() => {
    if (searchable) searchRef.current?.focus();
    else focusStop(0);
  }, [searchable]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const matches = useMemo(() => {
    if (!defs) return [];
    const q = query.trim().toLowerCase();
    const list = q
      ? defs.filter((d) => `${d.name} ${d.id} ${d.category} ${d.summary}`.toLowerCase().includes(q))
      : defs;
    return list.slice(0, 9);
  }, [defs, query]);

  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, matches.length - 1)));
  }, [matches.length]);

  const pickActive = () => {
    const d = matches[active];
    if (d && onPickNode) {
      onPickNode(d.id);
      onClose();
    }
  };

  // Keys the menu acts on stop here: a menu can sit inside a node in React
  // (a port's menu is portaled out of it only in the DOM), and the node's own
  // keys (arrows move it, Enter selects it, Escape deselects it) must not fire.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onClose();
      return;
    }
    const els = itemEls();
    const from = els.indexOf(e.target as HTMLElement);
    if (from < 0) return; // the search box handles its own keys
    if (e.key === "Tab") {
      // focus goes back to the opener, and Tab moves on from there
      onClose();
      return;
    }
    const to = menuFocusTarget(e.key, from, els.length, searchable);
    if (to !== null) {
      e.preventDefault();
      focusStop(to);
    }
    if (to !== null || e.key === "Enter" || e.key === " ") e.stopPropagation();
  };

  // while focus is on an item it follows the pointer, as in a native menu, so
  // ↑/↓ go on from the row under it; focus in the search box stays put.
  const followPointer = (e: React.MouseEvent<HTMLButtonElement>) => {
    const current = document.activeElement;
    if (current instanceof HTMLElement && current !== e.currentTarget && itemEls().includes(current)) {
      e.currentTarget.focus({ preventScroll: true });
    }
  };

  return (
    <div
      ref={ref}
      className="ctxmenu"
      style={{ left: pos.x, top: pos.y }}
      onContextMenu={(e) => e.preventDefault()}
      onKeyDown={onKeyDown}
    >
      {items.length > 0 && (
        <div role="menu" aria-label={label}>
          {items.map((it) => (
            <div key={it.id} role="none">
              {it.separatorBefore && <div className="ctx-sep" role="separator" />}
              <button
                role={it.active === undefined ? "menuitem" : "menuitemradio"}
                aria-checked={it.active}
                tabIndex={-1}
                className={`ctx-item ${it.danger ? "danger" : ""} ${it.disabled ? "disabled" : ""}`}
                disabled={it.disabled}
                onMouseEnter={followPointer}
                onClick={() => {
                  if (it.disabled) return;
                  it.run();
                  onClose();
                }}
              >
                {it.swatch
                  ? <span className="ctx-swatch" style={{ background: it.swatch }} />
                  : it.icon && <Icon name={it.icon} className="ctx-ico" />}
                <span className="ctx-lbl">{it.label}</span>
                {it.active && <Icon name="checkmark-outline" className="ctx-check" />}
                {it.kbd && <span className="ctx-kbd">{it.kbd}</span>}
              </button>
            </div>
          ))}
        </div>
      )}

      {searchable && (
        <div className="ctx-search-sec">
          {items.length > 0 && <div className="ctx-sep" />}
          <div className="ctx-search-title">{searchTitle ?? "Add node"}</div>
          <div className="ctx-search">
            <Icon name="search-outline" />
            <input
              ref={searchRef}
              value={query}
              placeholder="search nodes…"
              spellCheck={false}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setActive((a) => Math.min(matches.length - 1, a + 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  // ↑ from the top result goes up into the items above
                  if (active === 0) focusStop(menuFocusTarget("ArrowUp", itemEls().length, itemEls().length, true));
                  else setActive((a) => Math.max(0, a - 1));
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  pickActive();
                }
              }}
            />
          </div>
          <div className="ctx-results">
            {matches.length === 0 ? (
              <div className="ctx-empty">no match</div>
            ) : (
              matches.map((d, i) => {
                const fc = functionColorVar(d);
                return (
                  <button
                    key={d.id}
                    className={`ctx-node ${i === active ? "active" : ""}`}
                    onMouseEnter={() => setActive(i)}
                    onClick={() => {
                      onPickNode!(d.id);
                      onClose();
                    }}
                  >
                    <span className="ctx-node-ico" style={{ color: fc }}>
                      <Icon name={nodeIcon(d, hasIcon)} />
                    </span>
                    <span className="ctx-node-name">{d.name}</span>
                    <span className="ctx-node-cap">{capabilityHint(d)}</span>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
