// ============================================================================
// StatusBar: the bottom live line + expandable console.
// Collapsed: the latest event, flow counters, cursor coords, zoom, save state.
// Expanded: a scrolling console with level-coloured rows (full-row tint on
// warn/error, never a single-side stripe) and filter chips: the levels, and
// "values", which adds the full value stream to the lines worth reading
// (lib/consoleFeed). Rows are memoised by entry, so an event redraws only the
// row it adds or counts, and the view follows new lines only while it sits at
// the bottom.
//
// Nothing in the collapsed line overlaps at any width: the counters and the
// right-hand items keep their room (narrow bars drop the cursor coordinates,
// then the counter words, by container query), and the latest line shrinks
// first: its message ellipsizes to a readable minimum, and answer buttons that
// still do not fit collapse into one "N choices" button (lib/statusFit) that
// opens the console, where the line shows in full with its buttons.
// ============================================================================
import { memo, useLayoutEffect, useMemo, useRef, useState } from "react";
import { choicesLabel, offerFits } from "../lib/statusFit";
import { Icon } from "../lib/icons";
import { latestEntry, visibleEntries, type ConsoleEntry, type ConsoleFeed, type ConsoleLevel, type NoticeAction } from "../lib/consoleFeed";

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
  // a line still waiting on a choice (its buttons) holds the status line until
  // the choice is made, so newer lines never push it out of sight.
  const offer = useMemo(() => [...log.readable].reverse().find((e) => e.actions && e.actions.length > 0), [log]);
  const tail = useMemo(() => offer ?? latestEntry(entries), [offer, entries]);

  // the line's buttons at full size only while they fit beside the readable
  // minimum of its message; measured from the bar itself (lib/statusFit).
  const tailRef = useRef<HTMLDivElement>(null);
  const offerMeasureRef = useRef<HTMLSpanElement>(null);
  const msgMinRef = useRef<HTMLSpanElement>(null);
  const [collapsed, setCollapsed] = useState(false);
  const offerKey = tail?.actions?.map((a) => a.label).join("\u0000") ?? "";
  useLayoutEffect(() => {
    const el = tailRef.current;
    if (!el || !offerKey) { setCollapsed(false); return; }
    const fit = () => {
      const measure = offerMeasureRef.current;
      const probe = msgMinRef.current;
      if (!measure || !probe) return;
      const gap = parseFloat(getComputedStyle(el).columnGap) || 0;
      const fixed = [...el.querySelectorAll<HTMLElement>(":scope > .ts, :scope > .lvl")]
        .reduce((w, x, i) => w + x.offsetWidth + (i ? gap : 0), 0);
      // the readable minimum, read off a probe (the message itself drops its
      // minimum once the buttons collapse, so it cannot be the yardstick)
      const msgMin = probe.offsetWidth;
      const next = !offerFits(el.clientWidth, fixed, msgMin, measure.offsetWidth, gap);
      setCollapsed((was) => (was === next ? was : next));
    };
    fit();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [offerKey]);

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
        <div className={"sb-tail" + (collapsed && tail?.actions ? " collapsed" : "")} ref={tailRef}>
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
              {tail.actions && (collapsed ? (
                <span className="log-offer">
                  <button
                    type="button"
                    className="chip"
                    title={`${tail.actions.map((a) => a.label).join(" · ")}: open the console to choose`}
                    onClick={() => setOpen(true)}
                  >
                    {choicesLabel(tail.actions.length)}
                  </button>
                </span>
              ) : <OfferButtons actions={tail.actions} />)}
              {/* the buttons at full size, measured out of sight, so the line
                  knows when they fit again as the bar widens */}
              {tail.actions && (
                <span className="log-offer sb-measure" ref={offerMeasureRef} aria-hidden="true">
                  {tail.actions.map((a) => <span key={a.label} className="chip">{a.label}</span>)}
                </span>
              )}
              {tail.actions && <span className="sb-measure sb-msg-min" ref={msgMinRef} aria-hidden="true" />}
            </>
          ) : (
            <span className="empty">idle · no events streamed yet</span>
          )}
        </div>

        <div className="sb-mid">
          <span className="ti" title={`${nodeCount} node${nodeCount === 1 ? "" : "s"} on this canvas`}>
            <Icon name="cube-outline" />
            <b>{nodeCount}</b><span className="tl"> nodes</span>
          </span>
          <span className={`ti ${running ? "live" : ""}`} title={`${eventsPerSec.toFixed(1)} events per second`}>
            <Icon name="pulse-outline" style={running ? { color: "var(--accent)" } : undefined} />
            <b>{eventsPerSec.toFixed(1)}</b><span className="tl"> ev/s</span>
          </span>
          <span className="ti" title={`${inFlight} in flight`}>
            <Icon name="layers-outline" />
            <b>{inFlight}</b><span className="tl"> in-flight</span>
          </span>
        </div>

        <div className="sb-right">
          <span className="coord cursor">
            x <b>{cursor.x}</b> · y <b>{cursor.y}</b>
          </span>
          <span className="coord">
            zoom <b>{Math.round(zoom * 100)}%</b>
          </span>
          <button className={`sb-btn ${open ? "active" : ""}`} onClick={() => setOpen((o) => !o)} title="console">
            <Icon name="terminal-outline" /><span className="tl"> console</span>
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
      {entry.actions && <OfferButtons actions={entry.actions} />}
    </div>
  );
});

/** The answers a console line offers, as the console's own chips. */
function OfferButtons({ actions }: { actions: NoticeAction[] }) {
  return (
    <span className="log-offer">
      {actions.map((a) => (
        <button key={a.label} type="button" className="chip" onClick={a.run}>{a.label}</button>
      ))}
    </span>
  );
}

function agoLabel(ts: number): string {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  return `${m}m ago`;
}
