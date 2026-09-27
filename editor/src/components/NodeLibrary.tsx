// ============================================================================
// NodeLibrary: the left palette. Groups the fetched node defs
// by kind-group, each row a draggable + clickable entry with the kind glyph,
// name, and a mono capability hint. Searchable, with collapsible groups.
// ============================================================================
import { useMemo, useState, type CSSProperties } from "react";
import type { NodeDef } from "../types/protocol";
import { Icon, hasIcon } from "../lib/icons";
import {
  GROUP_ORDER,
  groupColorVar,
  functionColorVar,
  libraryGroup,
  nodeIcon,
} from "../lib/kinds";
import { capabilityHint } from "../lib/nodeMeta";

interface NodeLibraryProps {
  defs: NodeDef[];
  loading: boolean;
  error: string | null;
  onAdd: (typeId: string) => void;
  onClose: () => void;
}

/** Highlight the matched substring of a name (case-insensitive). */
function highlight(name: string, query: string) {
  if (!query) return name;
  const i = name.toLowerCase().indexOf(query.toLowerCase());
  if (i < 0) return name;
  return (
    <>
      {name.slice(0, i)}
      <mark>{name.slice(i, i + query.length)}</mark>
      {name.slice(i + query.length)}
    </>
  );
}

export function NodeLibrary({ defs, loading, error, onAdd, onClose }: NodeLibraryProps) {
  void onClose; // collapse is now triggered from the Workflows header at the top of the rail
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase();
    const buckets = new Map<string, NodeDef[]>();
    for (const def of defs) {
      if (q) {
        const hay = `${def.name} ${def.id} ${def.summary} ${def.category}`.toLowerCase();
        if (!hay.includes(q)) continue;
      }
      const group = libraryGroup(def);
      if (!buckets.has(group)) buckets.set(group, []);
      buckets.get(group)!.push(def);
    }
    // order groups by GROUP_ORDER, then any extras alphabetically
    const ordered: Array<[string, NodeDef[]]> = [];
    for (const g of GROUP_ORDER) {
      if (buckets.has(g)) ordered.push([g, buckets.get(g)!]);
    }
    for (const [g, list] of [...buckets.entries()].sort()) {
      if (!GROUP_ORDER.includes(g)) ordered.push([g, list]);
    }
    for (const [, list] of ordered) list.sort((a, b) => a.name.localeCompare(b.name));
    return ordered;
  }, [defs, query]);

  const total = grouped.reduce((n, [, list]) => n + list.length, 0);

  const onDragStart = (e: React.DragEvent, typeId: string) => {
    e.dataTransfer.setData("application/boltjar-node", typeId);
    e.dataTransfer.effectAllowed = "copy";
  };

  return (
    <aside className="library">
      <div className="rail-head">
        {/* The close (×) for the WHOLE left rail now lives in the WORKFLOWS
            section header at the top of the rail (single close for the stacked
            two-pane rail). Here we only carry the quiet section label. */}
        <div className="rail-head-row">
          <div className="eyebrow">Node Library</div>
        </div>
        <div className="search-field">
          <Icon name="search-outline" />
          <input
            type="text"
            placeholder="filter nodes…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            spellCheck={false}
          />
          {query && (
            <span className="clearx" title="clear" onClick={() => setQuery("")}>
              <Icon name="close-outline" style={{ width: 13, height: 13 }} />
            </span>
          )}
        </div>
      </div>

      <div className="lib-scroll">
        {loading && <div className="lib-empty">loading node library…</div>}
        {error && <div className="lib-empty">library unavailable · {error}</div>}
        {!loading && !error && total === 0 && (
          <div className="lib-empty">no nodes match “{query}”.</div>
        )}

        {grouped.map(([group, list]) => {
          const isCollapsed = collapsed[group] ?? false;
          return (
            <div className="lib-group" key={group}>
              <button
                className={`group-head ${isCollapsed ? "collapsed" : ""}`}
                onClick={() => setCollapsed((c) => ({ ...c, [group]: !isCollapsed }))}
              >
                <span className="gd" style={{ background: groupColorVar(group) }} />
                <span className="gname">{group}</span>
                <span className="count">{list.length}</span>
                <Icon name="chevron-down-outline" className="chev" />
              </button>
              {!isCollapsed &&
                list.map((def) => {
                  const fc = functionColorVar(def);
                  return (
                    <button
                      className="lib-item"
                      key={def.id}
                      draggable
                      onDragStart={(e) => onDragStart(e, def.id)}
                      onClick={() => onAdd(def.id)}
                      title={def.summary || def.id}
                    >
                      <div
                        className="li-ico"
                        style={
                          {
                            background: `color-mix(in srgb, ${fc} 13%, transparent)`,
                          } as CSSProperties
                        }
                      >
                        <Icon name={nodeIcon(def, hasIcon)} style={{ color: fc } as CSSProperties} />
                      </div>
                      <div className="li-text">
                        <div className="li-name">{highlight(def.name, query)}</div>
                        <div className="li-cap">{capabilityHint(def)}</div>
                      </div>
                      <Icon name="reorder-two-outline" className="drag-grip" />
                    </button>
                  );
                })}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
