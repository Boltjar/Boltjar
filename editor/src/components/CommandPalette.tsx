// ============================================================================
// CommandPalette: the ⌘K overlay. Grouped results (Add node,
// Actions, Help, Go to node) over a scrim, keyboard-navigable (↑/↓/Enter/Esc), with
// the active row carrying a full accent ring (never a single-side stripe) and a
// forward marker.
// ============================================================================
import { useEffect, useMemo, useRef, useState } from "react";
import type { NodeDef } from "../types/protocol";
import type { WFNode } from "../lib/graphAdapter";
import { Icon } from "../lib/icons";
import { libraryGroup, nodeIcon } from "../lib/kinds";
import { capabilityHint } from "../lib/nodeMeta";
import { typesCompatible } from "../lib/types";

export interface PaletteAction {
  id: string;
  label: string;
  hint: string;
  icon: string;
  kbd?: string;
  /** the heading it lists under: "Actions" (default) or "Help". A query that
   *  names the group ("help") finds all of its actions. */
  group?: "Actions" | "Help";
  run: () => void;
}

interface CommandPaletteProps {
  defs: NodeDef[];
  nodes: WFNode[];
  actions: PaletteAction[];
  onAddNode: (typeId: string) => void;
  onGoToNode: (id: string) => void;
  onClose: () => void;
  /** When set (the user dropped a wire on empty space), only show nodes whose
   *  port in the given direction is compatible with the data type. Adds a
   *  one-line header so the filter is visible. */
  typeFilter?: { type: string; direction: "input" | "output" } | null;
}

interface Row {
  key: string;
  group: string;
  icon: string;
  label: string;
  hint: string;
  kbd?: string;
  run: () => void;
}

export function CommandPalette(props: CommandPaletteProps) {
  const { defs, nodes, actions, onAddNode, onGoToNode, onClose, typeFilter } = props;
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const rows = useMemo<Row[]>(() => {
    const q = query.trim().toLowerCase();
    const match = (s: string) => !q || s.toLowerCase().includes(q);

    // when a type filter is in effect, only show nodes whose ports on the
    // requested side accept (or emit) a value compatible with the source type.
    const compatibleByFilter = (d: NodeDef): boolean => {
      if (!typeFilter) return true;
      const ports = typeFilter.direction === "input" ? d.inputs : d.outputs;
      return ports.some((p) => typesCompatible(
        typeFilter.direction === "input" ? typeFilter.type : p.type,
        typeFilter.direction === "input" ? p.type : typeFilter.type,
      ));
    };
    const addRows: Row[] = defs
      .filter(compatibleByFilter)
      .filter((d) => match(`${d.name} ${d.id} ${d.category} ${d.summary}`))
      .slice(0, typeFilter ? 24 : 8)
      .map((d) => ({
        key: `add:${d.id}`,
        group: "Add node",
        icon: nodeIcon(d),
        label: d.name,
        hint: `${libraryGroup(d).toLowerCase()} · ${capabilityHint(d)}`,
        run: () => {
          onAddNode(d.id);
          onClose();
        },
      }));

    const actionRows: Row[] = actions
      .filter((a) => match(`${a.label} ${a.hint} ${a.group ?? ""}`))
      .map((a) => ({
        key: `act:${a.id}`,
        group: a.group ?? "Actions",
        icon: a.icon,
        label: a.label,
        hint: a.hint,
        kbd: a.kbd,
        run: () => {
          a.run();
          onClose();
        },
      }));

    const gotoRows: Row[] = nodes
      .filter((n) => match(n.id))
      .slice(0, 6)
      .map((n) => {
        const def = defs.find((d) => d.id === n.data.typeId);
        return {
          key: `go:${n.id}`,
          group: "Go to node",
          icon: def ? nodeIcon(def) : "cube-outline",
          label: n.id,
          hint: `#${n.id} · ${def ? libraryGroup(def).toLowerCase() : "node"}`,
          kbd: "↵",
          run: () => {
            onGoToNode(n.id);
            onClose();
          },
        };
      });

    return [...addRows, ...actionRows, ...gotoRows];
  }, [defs, nodes, actions, query, onAddNode, onGoToNode, onClose]);

  // clamp active index whenever the result set changes
  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, rows.length - 1)));
  }, [rows.length]);

  // group the flat rows for rendering while keeping a global active index
  const groups = useMemo(() => {
    const order = ["Add node", "Actions", "Help", "Go to node"];
    const byGroup = new Map<string, { row: Row; index: number }[]>();
    rows.forEach((row, index) => {
      if (!byGroup.has(row.group)) byGroup.set(row.group, []);
      byGroup.get(row.group)!.push({ row, index });
    });
    return order.filter((g) => byGroup.has(g)).map((g) => ({ name: g, items: byGroup.get(g)! }));
  }, [rows]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(rows.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      rows[active]?.run();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  // keep the active row in view
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="palette" role="dialog" aria-modal="true">
        {typeFilter && (
          <div className="pal-filter">
            <Icon name="funnel-outline" />
            <span>
              Nodes that accept <b>{typeFilter.type}</b> on an <b>{typeFilter.direction}</b>
            </span>
          </div>
        )}
        <div className="pal-input">
          <Icon name="search-outline" />
          <input
            ref={inputRef}
            type="text"
            placeholder={typeFilter ? `search ${typeFilter.type}-compatible nodes…` : "search nodes, actions, go to…"}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            spellCheck={false}
          />
          <span className="pal-esc">ESC</span>
        </div>

        <div className="pal-scroll" ref={listRef}>
          {rows.length === 0 ? (
            <div className="pal-empty">no matches for “{query}”.</div>
          ) : (
            groups.map((group) => (
              <div className="pal-group" key={group.name}>
                <div className="pal-grouplbl">{group.name}</div>
                {group.items.map(({ row, index }) => (
                  <div
                    key={row.key}
                    data-idx={index}
                    className={`pal-row ${index === active ? "active" : ""}`}
                    onMouseEnter={() => setActive(index)}
                    onClick={row.run}
                  >
                    <div className="pr-ico">
                      <Icon name={row.icon} />
                    </div>
                    <span className="pr-name">{row.label}</span>
                    <span className="pr-hint">{row.hint}</span>
                    {index === active ? (
                      <Icon name="arrow-forward-outline" className="pr-arrow" />
                    ) : row.kbd ? (
                      <span className="pr-kbd kbd">{row.kbd}</span>
                    ) : null}
                  </div>
                ))}
              </div>
            ))
          )}
        </div>
      </div>
    </>
  );
}
