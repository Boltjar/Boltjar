// ============================================================================
// StatusBar: the bottom live line + expandable console.
// Collapsed: the latest event, flow counters, cursor coords, zoom, save state.
// Expanded: a scrolling console with level-coloured rows (full-row tint on
// warn/error, never a single-side stripe) and filter chips: the levels, and
// "values", which adds the full value stream to the lines worth reading
// (lib/consoleFeed). Rows are memoised by entry, so an event redraws only the
// row it adds or counts, and the view follows new lines only while it sits at
// the bottom.
// ============================================================================
import { memo, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../lib/icons";
import { latestEntry, visibleEntries, type ConsoleEntry, type ConsoleFeed, type ConsoleLevel } from "../lib/consoleFeed";

interface StatusBarProps {
  log: ConsoleFeed;
  running: boolean;
  eventsPerSec: number;
  inFlight: number;
  /** total node count of the active graph (was on the top-left canvas
   *  overlay; moved here so the chrome stays uncluttered). */
  nodeCount: number;
  cursor: { x: number; y: number };
  zoom: number;
  dirty: boolean;
  lastSaved: number | null;
  onClear: () => void;
}

const LEVEL_ICON: Record<ConsoleLevel, string> = {
  info: "information-circle-outline",
  ok: "checkmark-circle",
  warn: "alert-circle-outline",
  bad: "close-circle",
};

export function StatusBar(props: StatusBarProps) {
  const { log, running, eventsPerSec, inFlight, nodeCount, cursor, zoom, dirty, lastSaved, onClear } = props;
  const [open, setOpen] = useState(false);
  const [levelFilter, setLevelFilter] = useState<Set<ConsoleLevel>>(new Set());
  const [showValues, setShowValues] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  // the view follows new lines only while it sits at the bottom
  const atBottom = useRef(true);

  const entries = useMemo(() => visibleEntries(log, showValues), [log, showValues]);
  const tail = useMemo(() => latestEntry(entries), [entries]);

  const filtered = useMemo(() => {
    if (levelFilter.size === 0) return entries;
    return entries.filter((l) => levelFilter.has(l.level));
  }, [entries, levelFilter]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (open && el && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [filtered, open]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (el) atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 8;
  };

  const toggleLevel = (lvl: ConsoleLevel) =>
    setLevelFilter((s) => {
      const next = new Set(s);
      if (next.has(lvl)) next.delete(lvl);
      else next.add(lvl);
      return next;
    });

  // lastSaved == null is just "no save HAS HAPPENED in this session" - it does
  // NOT mean the graph is unsaved (it likely came from the server/draft on
  // mount). Only call out an unsaved state when there are real edits (dirty).
  const savedLabel = dirty
    ? "unsaved"
    : lastSaved == null
      ? "saved"
      : `saved ${agoLabel(lastSaved)}`;

  return (
    <footer className="statusbar">
      {open && (
        <div className="console">
          <div className="console-bar">
            <span className="ct">Console</span>
            <div className="filters">
              <button
                className={`chip ${showValues ? "on" : ""}`}
                onClick={() => setShowValues((v) => !v)}
                title="add every value a port emits (they also show on the nodes)"
              >
                values
              </button>
              {(["info", "ok", "warn", "bad"] as const).map((lvl) => (
                <button
                  key={lvl}
                  className={`chip ${levelFilter.has(lvl) ? "on" : ""}`}
                  onClick={() => toggleLevel(lvl)}
                >
                  {lvl}
                </button>
              ))}
              <button className="chip" onClick={onClear} title="clear console">
                clear
              </button>
            </div>
          </div>
          <div className="console-scroll" ref={scrollRef} onScroll={onScroll}>
            {filtered.length === 0 ? (
              <div className="console-empty">
                {showValues ? "no events yet · run the graph to stream live values" : "no lines yet · logs, warnings and errors show here"}
              </div>
            ) : (
              filtered.map((entry) => <LogRow entry={entry} key={entry.id} />)
            )}
          </div>
        </div>
      )}

      <div className="sb-line">
        <div className="sb-tail">
          {tail ? (
            <>
              <span className="ts">{tail.ts}</span>
              <span className={`lvl ${tail.level}`}>
                <Icon name={LEVEL_ICON[tail.level]} />
              </span>
              <span className="msg">
                {tail.node && <span className="hl">{tail.node}</span>} {tail.message}
                {tail.count > 1 && ` ×${tail.count}`}
              </span>
            </>
          ) : (
            <span className="empty">idle · no events streamed yet</span>
          )}
        </div>

        <div className="sb-mid">
          <span className="ti" title={`${nodeCount} node${nodeCount === 1 ? "" : "s"} on this canvas`}>
            <Icon name="cube-outline" />
            <b>{nodeCount}</b> nodes
          </span>
          <span className={`ti ${running ? "live" : ""}`}>
            <Icon name="pulse-outline" style={running ? { color: "var(--accent)" } : undefined} />
            <b>{eventsPerSec.toFixed(1)}</b> ev/s
          </span>
          <span className="ti">
            <Icon name="layers-outline" />
            <b>{inFlight}</b> in-flight
          </span>
        </div>

        <div className="sb-right">
          <span className="coord">
            x <b>{cursor.x}</b> · y <b>{cursor.y}</b>
          </span>
          <span className="coord">
            zoom <b>{Math.round(zoom * 100)}%</b>
          </span>
          <button className={`sb-btn ${open ? "active" : ""}`} onClick={() => setOpen((o) => !o)}>
            <Icon name="terminal-outline" /> console
          </button>
          <span className={`sb-btn ${dirty ? "unsaved" : "saved"}`}>
            <Icon name="git-commit-outline" /> {savedLabel}
          </span>
        </div>
      </div>
    </footer>
  );
}

/** One console row; redrawn only when its entry changes (a new count or time). */
const LogRow = memo(function LogRow({ entry }: { entry: ConsoleEntry }) {
  return (
    <div className={`log-row ${entry.level}`}>
      <span className="lts">{entry.ts}</span>
      <span className="llvl">
        <Icon name={LEVEL_ICON[entry.level]} />
      </span>
      {entry.node && <span className="lnode">{entry.node}</span>}
      <span className="lmsg">{entry.message}</span>
      {entry.count > 1 && <span className="lcount">×{entry.count}</span>}
    </div>
  );
});

function agoLabel(ts: number): string {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  return `${m}m ago`;
}
