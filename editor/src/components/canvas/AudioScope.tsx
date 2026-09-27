// ============================================================================
// AudioScope: THE audio display. Every place the editor shows an audio value
// draws it through this one component (today: the Preview's audio player).
//
// A scope trace in the manner of an audio CHOP viewer: a dark plot the full
// width of its container, faint grid, a centre zero line, the clip's real
// min/max envelope in the wire's type colour, amplitude labels on the left and
// a time axis with the duration along the bottom.
//
//   data   the clip (data:/blob:/http url) is fetched and decoded ONCE per clip
//          (cached by clipKey, shared by every scope), mixed to mono and reduced
//          to CLIP_BINS min/max bins. A width change only re-reduces the bins.
//   draw   the grid, the trace (dim + bright copies) and the labels are painted
//          into offscreen layers when the clip, width or density changes; a frame
//          is a few drawImage calls. Nothing repaints unless the clip plays, is
//          sought, or the layers change, so a canvas full of scopes stays cheap.
//   live   while the element plays, a playhead follows it (requestAnimationFrame,
//          stopped on pause/end), the played part of the trace is bright and the
//          rest dim, and a slim bar at the playhead shows the current loudness
//          from an AnalyserNode (one MediaElementAudioSourceNode per element,
//          made once and reused). Click or drag on the plot to seek.
//   honest reading / cannot read / no samples (speech) states keep the same
//          height and show a flat centre line with a quiet caption.
// The maths lives in lib/audioScope.ts (tested); this file is DOM and Web Audio.
// ============================================================================
import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { typeColorVar } from "../../lib/types";
import {
  CLIP_BINS, type Envelope, ampScale, canTap, clipKey, envelope, formatAmp, levelOf, mixdown,
  peakOf, playheadX, reduceEnvelope, rms, seekTime, tickStep, timeLabels,
} from "../../lib/audioScope";

/** CSS pixel geometry of the scope: the plot, then the time axis under it. */
const PLOT_H = 58;
const AXIS_H = 14;
export const SCOPE_H = PLOT_H + AXIS_H;
/** the trace keeps this much air above and below full scale (CSS px) */
const PLOT_PAD = 4;
const LABEL_FONT_PX = 9;

// ---------------------------------------------------------------------------
// Decode cache: one decode per clip, shared by every scope on the page.
// ---------------------------------------------------------------------------
interface ClipPeaks {
  bins: Envelope;
  duration: number;
  peak: number;
}
const CACHE_MAX = 32;
const settled = new Map<string, ClipPeaks | null>();
const pending = new Map<string, Promise<ClipPeaks | null>>();

function remember(key: string, v: ClipPeaks | null) {
  settled.delete(key);
  settled.set(key, v);
  while (settled.size > CACHE_MAX) {
    const oldest = settled.keys().next().value;
    if (oldest === undefined) break;
    settled.delete(oldest);
  }
}

async function decodeClip(url: string): Promise<ClipPeaks | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    const bytes = await res.arrayBuffer();
    // an offline context decodes without asking for the audio device, so no
    // autoplay warning before the first click. It resamples to its own rate;
    // the duration is unchanged.
    const ctx = new OfflineAudioContext(1, 1, 44100);
    const buf = await ctx.decodeAudioData(bytes);
    const chans: Float32Array[] = [];
    for (let c = 0; c < buf.numberOfChannels; c++) chans.push(buf.getChannelData(c));
    const mono = mixdown(chans);
    const bins = envelope(mono, Math.min(CLIP_BINS, mono.length));
    return { bins, duration: buf.duration, peak: peakOf(bins) };
  } catch {
    return null;
  }
}

type ClipState =
  | { status: "none" }
  | { status: "reading" }
  | { status: "ready"; peaks: ClipPeaks }
  | { status: "error" };

function stateOf(key: string | null): ClipState {
  if (key === null) return { status: "none" };
  if (!settled.has(key)) return { status: "reading" };
  const v = settled.get(key);
  return v ? { status: "ready", peaks: v } : { status: "error" };
}

/** The decoded peaks of a clip, read from the shared cache or decoded once. */
function useClipPeaks(src: string | null): ClipState {
  const key = src ? clipKey(src) : null;
  const [, bump] = useState(0);
  useEffect(() => {
    if (!src || key === null || settled.has(key)) return;
    let alive = true;
    let job = pending.get(key);
    if (!job) {
      job = decodeClip(src).then((v) => {
        remember(key, v);
        pending.delete(key);
        return v;
      });
      pending.set(key, job);
    }
    void job.then(() => { if (alive) bump((n) => n + 1); });
    return () => { alive = false; };
  }, [src, key]);
  return stateOf(key);
}

// ---------------------------------------------------------------------------
// Live level: one AudioContext for the editor, one analyser tap per element.
// ---------------------------------------------------------------------------
let audioCtx: AudioContext | null = null;
interface Tap { source: MediaElementAudioSourceNode; analyser: AnalyserNode; buf: Float32Array<ArrayBuffer> }
const taps = new WeakMap<HTMLMediaElement, Tap>();

/** Create or resume the editor's AudioContext. Call it from a play click: a
 *  browser only lets audio start after the page has been interacted with. */
export function wakeAudio(): AudioContext | null {
  try {
    if (!audioCtx) audioCtx = new AudioContext();
    if (audioCtx.state === "suspended") void audioCtx.resume().catch(() => {});
    return audioCtx;
  } catch {
    return null;
  }
}

/** Route a playing element through an analyser, once per element. Only when
 *  the context runs (a routed element in a suspended context would go silent)
 *  and only for clips canTap allows (cross-origin ones would play silent). */
function tapElement(el: HTMLMediaElement): void {
  if (taps.has(el) || !canTap(el.currentSrc || el.src, window.location.origin)) return;
  const ctx = wakeAudio();
  if (!ctx) return;
  const route = () => {
    if (ctx.state !== "running" || taps.has(el)) return;
    try {
      const source = ctx.createMediaElementSource(el);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      analyser.connect(ctx.destination);
      taps.set(el, { source, analyser, buf: new Float32Array(analyser.fftSize) });
    } catch {
      // an element can be routed only once in its life; if it already was
      // (elsewhere), the scope simply shows no level.
    }
  };
  if (ctx.state === "running") route();
  else void ctx.resume().then(route, () => {});
}

/** Unhook an element that is leaving the page, so its audio nodes do not
 *  outlive it in the shared graph. Call it only for an element being thrown
 *  away: a routed element that is unhooked can never be routed again. */
export function releaseTap(el: HTMLMediaElement): void {
  const tap = taps.get(el);
  if (!tap) return;
  try {
    tap.source.disconnect();
    tap.analyser.disconnect();
  } catch {
    // already disconnected
  }
  taps.delete(el);
}

// ---------------------------------------------------------------------------
// Offscreen layers
// ---------------------------------------------------------------------------
interface Palette { trace: string; line: string; grid: string; ink: string; bright: string; bg: string; font: string }

function readPalette(el: HTMLElement): Palette {
  const cs = getComputedStyle(el);
  const v = (name: string) => cs.getPropertyValue(name).trim();
  return {
    trace: v("--pc") || v("--t-audio"),
    line: v("--line"),
    grid: v("--crosshair"),
    ink: v("--ink-faint"),
    bright: v("--ink-bright"),
    bg: v("--bg-2"),
    font: v("--font-mono") || "monospace",
  };
}

interface Layers {
  W: number;
  H: number;
  dpr: number;
  /** device px height of the plot (the axis sits below it) */
  plotH: number;
  under: HTMLCanvasElement;
  dim: HTMLCanvasElement | null;
  bright: HTMLCanvasElement | null;
  over: HTMLCanvasElement;
  palette: Palette;
}

function layer(W: number, H: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  return [c, c.getContext("2d")!];
}

function buildLayers(
  wCss: number, dpr: number, palette: Palette, peaks: ClipPeaks | null, duration: number, flatTrace: boolean,
): Layers {
  const W = Math.max(1, Math.round(wCss * dpr));
  const H = Math.round(SCOPE_H * dpr);
  const plotH = Math.round(PLOT_H * dpr);
  const mid = Math.round(plotH / 2);
  const pad = PLOT_PAD * dpr;
  const scale = peaks ? ampScale(peaks.peak) : 1;
  const font = `${LABEL_FONT_PX * dpr}px ${palette.font}`;
  const hair = Math.max(1, Math.round(dpr));

  // under: grid, zero line and the time axis
  const [under, u] = layer(W, H);
  u.fillStyle = palette.grid;
  for (const f of [0.5, -0.5]) {
    const y = Math.round(mid - f * (mid - pad));
    u.fillRect(0, y, W, hair);
  }
  u.font = font;
  const charW = u.measureText("0").width / dpr;
  const step = tickStep(duration, wCss);
  if (step > 0) {
    for (let t = step; t < duration - 1e-6; t += step) {
      const x = Math.round((t / duration) * W);
      u.fillRect(x, 0, hair, plotH);
    }
  }
  u.fillStyle = palette.line;
  u.fillRect(0, plotH, W, hair); // the axis rule under the plot
  u.globalAlpha = flatTrace ? 0.5 : 1;
  u.fillStyle = flatTrace ? palette.trace : palette.line;
  u.fillRect(0, mid, W, hair); // the zero line (in the trace colour when it IS the trace)
  u.globalAlpha = 1;
  u.fillStyle = palette.ink;
  u.textBaseline = "middle";
  const axisY = plotH + (H - plotH) / 2 + dpr * 0.5;
  for (const l of timeLabels(duration, wCss, charW)) {
    u.textAlign = l.align === "start" ? "left" : l.align === "end" ? "right" : "center";
    const x = l.align === "start" ? 3 * dpr : l.align === "end" ? W - 3 * dpr : l.x * dpr;
    if (!l.end) u.fillRect(Math.round(l.x * dpr), plotH, hair, Math.round(2 * dpr));
    u.fillText(l.text, x, axisY);
  }

  // the trace, one min/max bar per device column, painted twice (dim, bright)
  let dim: HTMLCanvasElement | null = null;
  let bright: HTMLCanvasElement | null = null;
  if (peaks) {
    const cols = reduceEnvelope(peaks.bins, W);
    const reach = mid - pad;
    const paint = (alpha: number) => {
      const [c, g] = layer(W, H);
      g.fillStyle = palette.trace;
      g.globalAlpha = alpha;
      for (let x = 0; x < W; x++) {
        const hi = Math.max(-1, Math.min(1, cols.max[x] / scale));
        const lo = Math.max(-1, Math.min(1, cols.min[x] / scale));
        const y1 = Math.round(mid - hi * reach);
        const y2 = Math.round(mid - lo * reach);
        g.fillRect(x, y1, 1, Math.max(hair, y2 - y1));
      }
      return c;
    };
    dim = paint(0.38);
    bright = paint(1);
  }

  // over: the amplitude labels, on a backing so the trace never runs through them
  const [over, o] = layer(W, H);
  o.font = font;
  o.textBaseline = "middle";
  o.textAlign = "left";
  const amp = [
    { text: formatAmp(scale), y: pad + LABEL_FONT_PX * dpr * 0.5 },
    { text: formatAmp(-scale), y: plotH - pad - LABEL_FONT_PX * dpr * 0.5 },
  ];
  for (const a of amp) {
    const w = o.measureText(a.text).width;
    o.globalAlpha = 0.82;
    o.fillStyle = palette.bg;
    o.fillRect(2 * dpr, a.y - LABEL_FONT_PX * dpr * 0.62, w + 4 * dpr, LABEL_FONT_PX * dpr * 1.24);
    o.globalAlpha = 1;
    o.fillStyle = palette.ink;
    o.fillText(a.text, 4 * dpr, a.y);
  }
  return { W, H, dpr, plotH, under, dim, bright, over, palette };
}

// fonts arrive after first paint; scopes repaint their labels once they do.
const fontsReady: Promise<unknown> =
  typeof document !== "undefined" && document.fonts ? document.fonts.ready : Promise.resolve();

// ---------------------------------------------------------------------------
// The component
// ---------------------------------------------------------------------------
export interface AudioScopeProps {
  /** the clip url (data:, blob: or http), or null when there are no samples */
  src: string | null;
  /** the port type the clip travels on; picks the trace colour (audio, pcm-audio) */
  type?: string;
  /** the element playing the clip: drives the playhead, seeking and the level */
  media?: HTMLMediaElement | null;
  /** what to say over a flat line when there is no clip to draw */
  note?: string;
}

export function AudioScope({ src, type = "audio", media = null, note }: AudioScopeProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const layersRef = useRef<Layers | null>(null);
  const mediaRef = useRef<HTMLMediaElement | null>(media);
  mediaRef.current = media;
  const levelRef = useRef(0);
  const rafRef = useRef(0);
  const dragRef = useRef(false);
  const [width, setWidth] = useState(0);
  const [fontTick, setFontTick] = useState(0);
  const [mediaDur, setMediaDur] = useState(0);
  const clip = useClipPeaks(src);
  const peaks = clip.status === "ready" ? clip.peaks : null;
  const duration = peaks ? peaks.duration : mediaDur;
  const peaksDur = useRef(0);
  peaksDur.current = peaks ? peaks.duration : 0;

  // the full inner width of whatever holds the scope
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      setWidth((prev) => (Math.abs(prev - w) < 0.5 ? prev : w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    let alive = true;
    void fontsReady.then(() => { if (alive) setFontTick((n) => n + 1); });
    return () => { alive = false; };
  }, []);

  /** Compose one frame from the cached layers (a handful of drawImage calls). */
  const drawFrame = useCallback(() => {
    const cv = canvasRef.current;
    const L = layersRef.current;
    if (!cv || !L) return;
    const g = cv.getContext("2d");
    if (!g) return;
    if (cv.width !== L.W || cv.height !== L.H) {
      cv.width = L.W;
      cv.height = L.H;
    }
    g.clearRect(0, 0, L.W, L.H);
    g.drawImage(L.under, 0, 0);
    const m = mediaRef.current;
    const dur = peaksDur.current || (m && Number.isFinite(m.duration) ? m.duration : 0);
    const t = m ? m.currentTime : 0;
    // "active": playing, or paused part-way (a sought or stopped-mid position)
    const active = !!m && dur > 0 && !m.ended && (!m.paused || t > 0);
    const px = active ? Math.round(playheadX(t, dur, L.W)) : 0;
    if (L.bright && L.dim) {
      if (active) {
        g.drawImage(L.dim, 0, 0);
        if (px > 0) g.drawImage(L.bright, 0, 0, px, L.H, 0, 0, px, L.H);
      } else {
        g.drawImage(L.bright, 0, 0);
      }
    }
    g.drawImage(L.over, 0, 0);
    if (active) {
      const hair = Math.max(1, Math.round(L.dpr));
      const x = Math.min(L.W - hair, px);
      g.globalAlpha = 0.75;
      g.fillStyle = L.palette.bright;
      g.fillRect(x, 0, hair, L.plotH);
      g.globalAlpha = 1;
      // the live level: a slim bar on the playhead, centred on the zero line
      const lvl = levelRef.current;
      if (lvl > 0.02) {
        const mid = L.plotH / 2;
        const h = Math.max(2 * L.dpr, lvl * (L.plotH - 2 * PLOT_PAD * L.dpr));
        const bw = 3 * L.dpr;
        g.save();
        g.shadowColor = L.palette.trace;
        g.shadowBlur = 8 * L.dpr * lvl;
        g.fillStyle = L.palette.trace;
        g.fillRect(Math.round(x + hair / 2 - bw / 2), Math.round(mid - h / 2), Math.round(bw), Math.round(h));
        g.restore();
      }
    }
  }, []);
  // rebuild the layers when the clip, width, colour or fonts change
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || width <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    // the centre line IS the trace when there are no samples to draw
    const flat = clip.status === "error" || (clip.status === "none" && !!note);
    layersRef.current = buildLayers(width, dpr, readPalette(el), peaks, duration, flat);
    drawFrame();
  }, [width, peaks, duration, clip.status, note, type, fontTick, drawFrame]);

  // follow the element: a rAF loop only while it plays, one frame on any seek
  useEffect(() => {
    const m = media;
    if (!m) {
      drawFrame();
      return;
    }
    const stopLoop = () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    };
    const loop = () => {
      const tap = taps.get(m);
      if (tap) {
        tap.analyser.getFloatTimeDomainData(tap.buf);
        const now = levelOf(rms(tap.buf));
        levelRef.current = Math.max(now, levelRef.current * 0.86);
      }
      drawFrame();
      rafRef.current = requestAnimationFrame(loop);
    };
    const onPlay = () => {
      tapElement(m);
      stopLoop();
      rafRef.current = requestAnimationFrame(loop);
    };
    const onHalt = () => {
      stopLoop();
      levelRef.current = 0;
      drawFrame();
    };
    const onDur = () => setMediaDur(Number.isFinite(m.duration) ? m.duration : 0);
    const onSeek = () => { if (m.paused) drawFrame(); };
    m.addEventListener("play", onPlay);
    m.addEventListener("pause", onHalt);
    m.addEventListener("ended", onHalt);
    m.addEventListener("emptied", onHalt);
    m.addEventListener("seeked", onSeek);
    m.addEventListener("timeupdate", onSeek);
    m.addEventListener("durationchange", onDur);
    onDur();
    if (!m.paused) onPlay();
    else drawFrame();
    return () => {
      stopLoop();
      m.removeEventListener("play", onPlay);
      m.removeEventListener("pause", onHalt);
      m.removeEventListener("ended", onHalt);
      m.removeEventListener("emptied", onHalt);
      m.removeEventListener("seeked", onSeek);
      m.removeEventListener("timeupdate", onSeek);
      m.removeEventListener("durationchange", onDur);
    };
  }, [media, drawFrame]);

  const seekable = !!media && duration > 0;
  const seekTo = (e: PointerEvent<HTMLCanvasElement>) => {
    const m = mediaRef.current;
    const cv = canvasRef.current;
    if (!m || !cv || !(duration > 0)) return;
    const r = cv.getBoundingClientRect(); // scaled by the canvas zoom; the fraction is not
    m.currentTime = seekTime(e.clientX - r.left, r.width, duration);
    drawFrame();
  };
  const onDown = (e: PointerEvent<HTMLCanvasElement>) => {
    if (!seekable || e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    dragRef.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    seekTo(e);
  };
  const onMove = (e: PointerEvent<HTMLCanvasElement>) => {
    if (dragRef.current) seekTo(e);
  };
  const onUp = (e: PointerEvent<HTMLCanvasElement>) => {
    if (!dragRef.current) return;
    dragRef.current = false;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  };

  const caption =
    clip.status === "reading" ? "reading audio"
      : clip.status === "error" ? "cannot read this audio"
        : clip.status === "none" ? note
          : undefined;

  return (
    <div
      ref={wrapRef}
      className={`ascope nodrag${seekable ? " seekable" : ""}`}
      style={{ ["--pc" as string]: typeColorVar(type) } as CSSProperties}
    >
      <canvas
        ref={canvasRef}
        className="ascope-canvas"
        style={{ height: SCOPE_H }}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
      />
      {caption && (
        <div className="ascope-note" style={{ height: PLOT_H }}>
          <span>{caption}</span>
        </div>
      )}
    </div>
  );
}
