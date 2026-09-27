// ============================================================================
// Preview renderers: the live tap. The Preview node subscribes to the value
// stream of the wire feeding its `in` port (its single upstream source+port) and
// renders that value, adapting to the inferred type:
//   text   → a scrollable terminal box (history, autoscroll)
//   number → a small sparkline over recent values
//   json   → a collapsible tree
//   bool   → a lamp
//   event  → a pulse blink + tick read-out
//   default→ formatted mono text
// All inputs are values already serialised by the runtime's `_preview`.
// ============================================================================
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { HistPoint } from "../../hooks/useRunSocket";
import { Knob } from "./Knob";

export type PreviewType = "text" | "number" | "json" | "bool" | "event" | "audio" | "any";

/** Decide which renderer to use from the upstream port type + the value shape. */
export function previewTypeFor(portType: string | undefined, value: unknown): PreviewType {
  if (portType === "event") return "event";
  if (portType === "audio" || portType === "pcm-audio") return "audio";
  if (portType === "bool" || typeof value === "boolean") return "bool";
  if (portType === "int" || portType === "float" || portType === "number" || typeof value === "number") return "number";
  if (portType === "json" || portType === "list" || (value !== null && typeof value === "object")) return "json";
  if (portType === "text" || typeof value === "string") return "text";
  return "any";
}

interface PreviewProps {
  type: PreviewType;
  history: HistPoint[];
  latest: HistPoint | undefined;
  /** the Preview node's autoplay config (only the audio renderer uses it). */
  autoplay: boolean;
  onAutoplay: (v: boolean) => void;
}

export function PreviewBody({ type, history, latest, autoplay, onAutoplay }: PreviewProps) {
  // an audio preview always shows its player (with the Autoplay knob), even before
  // the first clip arrives, so autoplay can be set up ahead of time.
  if (type === "audio") {
    return <AudioPlayer value={latest?.value} autoplay={autoplay} onAutoplay={onAutoplay} />;
  }
  if (latest === undefined && history.length === 0) {
    return <div className="pv-empty">waiting for a value…</div>;
  }
  switch (type) {
    case "number":
      return <NumberSpark history={history} latest={latest} />;
    case "bool":
      return <BoolLamp value={Boolean(latest?.value)} />;
    case "event":
      return <EventPulse history={history} />;
    case "json":
      return <JsonTree value={latest?.value} />;
    case "text":
    default:
      return <TextTerminal history={history} />;
  }
}

/** Shows ONLY the most recent text value on the wire. Past results are not
 *  accumulated here; the Console (StatusBar) keeps the running log if you
 *  need history. */
function TextTerminal({ history }: { history: HistPoint[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const latest = history[history.length - 1];
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = 0;
  }, [latest?.at]);
  if (!latest) return <div className="pv-empty">waiting for a value…</div>;
  return (
    <div className="pv-term nodrag nowheel" ref={ref}>
      <span className="pv-txt">{String(latest.value)}</span>
    </div>
  );
}

/** A compact sparkline over the recent numeric history, with the latest value. */
function NumberSpark({ history, latest }: { history: HistPoint[]; latest: HistPoint | undefined }) {
  const nums = history.map((h) => Number(h.value)).filter((n) => Number.isFinite(n));
  const path = useMemo(() => sparkPath(nums, 220, 40), [nums]);
  const min = nums.length ? Math.min(...nums) : 0;
  const max = nums.length ? Math.max(...nums) : 0;
  return (
    <div className="pv-spark">
      <svg viewBox="0 0 220 40" preserveAspectRatio="none" className="pv-sparksvg">
        <polyline className="pv-sparkfill" points={`0,40 ${path} 220,40`} />
        <polyline className="pv-sparkline" points={path} />
      </svg>
      <div className="pv-sparkmeta">
        <span className="pv-now">{fmtNum(latest?.value)}</span>
        <span className="pv-range">{fmtNum(min)} – {fmtNum(max)}</span>
      </div>
    </div>
  );
}

/** A lamp that glows green for true, dim for false. */
function BoolLamp({ value }: { value: boolean }) {
  return (
    <div className={`pv-lamp ${value ? "on" : "off"}`}>
      <span className="pv-bulb" />
      <span className="pv-lampval">{value ? "true" : "false"}</span>
    </div>
  );
}

/** A pulse blink + the recent tick rate for an event stream. */
function EventPulse({ history }: { history: HistPoint[] }) {
  const last = history[history.length - 1];
  const fresh = last && Date.now() - last.at < 900;
  // estimate tick interval from the last few timestamps
  const recent = history.slice(-6).map((h) => h.at);
  let rate = "n/a";
  if (recent.length >= 2) {
    const spans: number[] = [];
    for (let i = 1; i < recent.length; i++) spans.push(recent[i] - recent[i - 1]);
    const avg = spans.reduce((a, b) => a + b, 0) / spans.length;
    rate = avg > 0 ? `${(1000 / avg).toFixed(1)}/s` : "n/a";
  }
  return (
    <div className="pv-event">
      <span className={`pv-pulse ${fresh ? "fresh" : ""}`} />
      <span className="pv-eventmeta">
        <b>{history.length}</b> ticks · {rate}
      </span>
      {last !== undefined && last.value !== undefined && String(last.value) !== "" && (
        <span className="pv-payload" title={String(last.value)}>{String(last.value)}</span>
      )}
    </div>
  );
}

/** If the value is a playable clip reference (data/blob/http url), return it. */
function audioUrl(v: unknown): string | null {
  if (typeof v !== "string") return null;
  return /^(data:audio|blob:|https?:)/i.test(v.trim()) ? v.trim() : null;
}

/** The speakable text behind an audio value (tolerates the legacy `<audio: …>`
 *  mock wrapper). A mock TTS emits the line to say; the player voices it. */
function audioText(v: unknown): string {
  // a real clip (data/blob/http url) has no speakable text behind it; the <audio>
  // plays it and the caption falls back to "audio" rather than dumping the url.
  if (audioUrl(v)) return "";
  let s = typeof v === "string" ? v : String(v ?? "");
  const m = /^<audio:\s*([\s\S]*?)>?$/.exec(s.trim());
  if (m) s = m[1];
  return s.trim();
}

/** An audio result: a play/stop button, an equaliser waveform that animates
 *  while playing, and an autoplay toggle. A real clip url plays through <audio>;
 *  a mock TTS line is voiced with the browser's speech synthesis, so the graph
 *  actually speaks. Autoplay voices each freshly arrived clip once. */
function AudioPlayer({ value, autoplay, onAutoplay }: { value: unknown; autoplay: boolean; onAutoplay: (v: boolean) => void }) {
  const url = audioUrl(value);
  const text = useMemo(() => audioText(value), [value]);
  const [speaking, setSpeaking] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const lastPlayed = useRef<string | null>(null);

  const stop = useCallback(() => {
    window.speechSynthesis?.cancel();
    audioRef.current?.pause();
    setSpeaking(false);
  }, []);

  const play = useCallback(() => {
    if (url) {
      const a = audioRef.current;
      if (a) { a.currentTime = 0; void a.play().catch(() => {}); }
      return;
    }
    const synth = window.speechSynthesis;
    if (!synth || !text) return;
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.onstart = () => setSpeaking(true);
    u.onend = () => setSpeaking(false);
    u.onerror = () => setSpeaking(false);
    synth.speak(u);
  }, [url, text]);

  // voice each newly arrived clip once (not on every re-render)
  useEffect(() => {
    const id = url ?? text;
    if (!id || lastPlayed.current === id) return;
    lastPlayed.current = id;
    if (autoplay) play();
  }, [url, text, autoplay, play]);

  // stop any speech if the node unmounts
  useEffect(() => () => { window.speechSynthesis?.cancel(); }, []);

  return (
    <div className="pv-audio nodrag">
      <div className="pv-audio-row">
        <button
          className={`pv-audio-btn ${speaking ? "playing" : ""}`}
          onClick={() => (speaking ? stop() : play())}
          title={speaking ? "stop" : "play"}
        >
          {speaking ? "◼" : "▶"}
        </button>
        <div className={`pv-wave ${speaking ? "playing" : ""}`} aria-hidden="true">
          {Array.from({ length: 13 }).map((_, i) => (
            <span key={i} style={{ animationDelay: `${(i % 7) * 0.08}s` }} />
          ))}
        </div>
      </div>
      <div className="pv-audio-cap" title={text}>{text || "audio"}</div>
      <Knob label="Autoplay" kind="bool" value={autoplay} onChange={(v) => onAutoplay(Boolean(v))} />
      {url && (
        <audio
          ref={audioRef}
          src={url}
          onPlay={() => setSpeaking(true)}
          onPause={() => setSpeaking(false)}
          onEnded={() => setSpeaking(false)}
        />
      )}
    </div>
  );
}

/** A small collapsible JSON tree (objects/arrays expandable). */
function JsonTree({ value }: { value: unknown }) {
  let parsed: unknown = value;
  if (typeof value === "string") {
    try {
      parsed = JSON.parse(value);
    } catch {
      parsed = value;
    }
  }
  return (
    <div className="pv-json nodrag nowheel">
      <JsonNode k={null} v={parsed} depth={0} />
    </div>
  );
}

function JsonNode({ k, v, depth }: { k: string | null; v: unknown; depth: number }) {
  const [open, setOpen] = useState(depth < 1);
  const isObj = v !== null && typeof v === "object";
  if (!isObj) {
    return (
      <div className="jn" style={{ paddingLeft: depth * 12 }}>
        {k !== null && <span className="jk">{k}:</span>}
        <span className={`jv ${typeof v}`}>{fmtLeaf(v)}</span>
      </div>
    );
  }
  const entries = Array.isArray(v)
    ? v.map((item, i) => [String(i), item] as const)
    : Object.entries(v as Record<string, unknown>);
  const brace = Array.isArray(v) ? ["[", "]"] : ["{", "}"];
  return (
    <div className="jn-group">
      <div className="jn jn-head" style={{ paddingLeft: depth * 12 }} onClick={() => setOpen((o) => !o)}>
        <span className={`jchev ${open ? "open" : ""}`}>▸</span>
        {k !== null && <span className="jk">{k}:</span>}
        <span className="jbrace">{brace[0]}{!open && ` ${entries.length} `}{!open && brace[1]}</span>
      </div>
      {open && (
        <>
          {entries.map(([ek, ev]) => (
            <JsonNode key={ek} k={ek} v={ev} depth={depth + 1} />
          ))}
          <div className="jn" style={{ paddingLeft: depth * 12 }}>
            <span className="jbrace">{brace[1]}</span>
          </div>
        </>
      )}
    </div>
  );
}

function fmtLeaf(v: unknown): string {
  if (v === null) return "null";
  if (typeof v === "string") return `"${v}"`;
  return String(v);
}

function fmtNum(v: unknown): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "n/a";
  return Math.abs(n) >= 1000 || Number.isInteger(n) ? n.toLocaleString() : n.toFixed(2);
}

/** Build an SVG polyline path normalising the series into a w×h box. */
function sparkPath(nums: number[], w: number, h: number): string {
  if (nums.length === 0) return "";
  if (nums.length === 1) return `0,${h / 2} ${w},${h / 2}`;
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = max - min || 1;
  const step = w / (nums.length - 1);
  return nums
    .map((n, i) => {
      const x = i * step;
      const y = h - ((n - min) / span) * (h - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}
