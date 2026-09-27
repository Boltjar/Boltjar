// ============================================================================
// audioScope: the pure maths behind the AudioScope trace (components/canvas/
// AudioScope.tsx). No DOM, no Web Audio: the component decodes a clip once,
// reduces it to a fixed set of min/max bins here, then asks for one min/max pair
// per device pixel column whenever its width changes. Time labels, seeking and
// the playhead are plain arithmetic over (x, width, duration), so every rule the
// scope draws by is tested in test/audio-scope.test.mjs.
// ============================================================================

/** A min/max envelope: column i spans the samples between min[i] and max[i]. */
export interface Envelope {
  min: Float32Array;
  max: Float32Array;
}

/** How many bins a decoded clip is reduced to before it is cached. Wider than
 *  any node the scope sits in at 2x pixel density, so drawing never needs the
 *  raw samples again. */
export const CLIP_BINS = 4096;

/** Mix any number of channels down to one by averaging them sample by sample.
 *  Channels of unequal length mix over the shortest one. */
export function mixdown(channels: ArrayLike<number>[]): Float32Array {
  if (channels.length === 0) return new Float32Array(0);
  if (channels.length === 1) return Float32Array.from(channels[0] as ArrayLike<number>);
  const n = Math.min(...channels.map((c) => c.length));
  const out = new Float32Array(n);
  const k = channels.length;
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let c = 0; c < k; c++) s += channels[c][i];
    out[i] = s / k;
  }
  return out;
}

/** Reduce an envelope (or raw samples, where min === max) to `columns` columns.
 *  Column i covers source entries [floor(i*n/columns), floor((i+1)*n/columns)),
 *  never fewer than one, so every source entry lands in exactly one column when
 *  there are more entries than columns, and each column repeats its nearest
 *  entry when there are fewer. */
export function reduceEnvelope(src: Envelope, columns: number): Envelope {
  const cols = Math.max(0, Math.floor(columns));
  const n = Math.min(src.min.length, src.max.length);
  const min = new Float32Array(cols);
  const max = new Float32Array(cols);
  if (n === 0 || cols === 0) return { min, max };
  for (let i = 0; i < cols; i++) {
    const start = Math.min(n - 1, Math.floor((i * n) / cols));
    const end = Math.max(start + 1, Math.min(n, Math.floor(((i + 1) * n) / cols)));
    let lo = src.min[start];
    let hi = src.max[start];
    for (let j = start + 1; j < end; j++) {
      if (src.min[j] < lo) lo = src.min[j];
      if (src.max[j] > hi) hi = src.max[j];
    }
    min[i] = lo;
    max[i] = hi;
  }
  return { min, max };
}

/** The min/max envelope of raw samples at `columns` columns. */
export function envelope(samples: Float32Array, columns: number): Envelope {
  return reduceEnvelope({ min: samples, max: samples }, columns);
}

/** The largest absolute value an envelope reaches (0 for silence or nothing). */
export function peakOf(env: Envelope): number {
  let p = 0;
  for (let i = 0; i < env.min.length; i++) {
    const a = Math.abs(env.min[i]);
    const b = Math.abs(env.max[i]);
    if (a > p) p = a;
    if (b > p) p = b;
  }
  return p;
}

/** The vertical full scale the trace is drawn at: the smallest of 0.1, 0.25,
 *  0.5 and 1 that holds the clip's peak, so a quiet clip still fills the plot
 *  and the amplitude labels read the real range. Silence and clipping use 1. */
export function ampScale(peak: number): number {
  if (!(peak > 0)) return 1;
  for (const s of [0.1, 0.25, 0.5]) if (peak <= s) return s;
  return 1;
}

/** An amplitude label: "+1", "-0.5", "+0.25". */
export function formatAmp(v: number): string {
  return `${v < 0 ? "-" : "+"}${Math.round(Math.abs(v) * 100) / 100}`;
}

/** A clock label, minutes:seconds with `decimals` digits after the second:
 *  formatClock(3.14, 1) = "0:03.1", formatClock(62, 0) = "1:02". Rounding
 *  happens before the split, so 59.96 s at one decimal reads "1:00.0". */
export function formatClock(seconds: number, decimals = 0): string {
  const d = Math.max(0, Math.floor(decimals));
  const scale = 10 ** d;
  const t = Math.round(Math.max(0, Number.isFinite(seconds) ? seconds : 0) * scale) / scale;
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  const [whole, frac] = s.toFixed(d).split(".");
  return `${m}:${whole.padStart(2, "0")}${frac !== undefined ? `.${frac}` : ""}`;
}

/** Steps (seconds) a time axis may use, smallest first. */
const TICK_STEPS = [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];

/** The smallest tick step that keeps ticks at least `minGap` pixels apart. */
export function tickStep(duration: number, width: number, minGap = 64): number {
  if (!(duration > 0) || !(width > 0)) return 0;
  for (const s of TICK_STEPS) if ((s / duration) * width >= minGap) return s;
  return TICK_STEPS[TICK_STEPS.length - 1];
}

export interface TickLabel {
  /** where the tick sits, in the same unit as `width` */
  x: number;
  text: string;
  /** how the label hangs off x: the first starts there, the duration ends there */
  align: "start" | "center" | "end";
  /** the duration read-out at the right edge (not a tick) */
  end?: boolean;
}

/** The labels along a scope's time axis: a tick every `tickStep` seconds from
 *  0:00, plus the clip's duration right-aligned at the far edge. A tick label
 *  that would touch its neighbour or the duration label is dropped (its grid
 *  line stays). `charW` is the width of one label character. */
export function timeLabels(duration: number, width: number, charW: number, minGap = 64): TickLabel[] {
  if (!(duration > 0) || !(width > 0)) return [];
  const step = tickStep(duration, width, minGap);
  const decimals = step < 1 ? (Math.round(step * 10) === step * 10 ? 1 : 2) : 0;
  const endText = formatClock(duration, 1);
  const pad = charW;
  const endLeft = width - endText.length * charW - pad;
  const out: TickLabel[] = [];
  let lastRight = -Infinity;
  for (let i = 0; ; i++) {
    const t = Math.round(i * step * 1000) / 1000;
    if (t >= duration) break;
    const x = (t / duration) * width;
    const text = formatClock(t, decimals);
    const w = text.length * charW;
    const align = i === 0 ? "start" : "center";
    const left = align === "start" ? x : x - w / 2;
    const right = left + w;
    if (left < lastRight + pad || right > endLeft) continue;
    out.push({ x, text, align });
    lastRight = right;
  }
  out.push({ x: width, text: endText, align: "end", end: true });
  return out;
}

/** The time `x` points at on a scope `width` wide showing `duration` seconds,
 *  clamped to the clip. */
export function seekTime(x: number, width: number, duration: number): number {
  if (!(width > 0) || !(duration > 0)) return 0;
  const f = Math.min(1, Math.max(0, x / width));
  return f * duration;
}

/** Where the playhead sits for `time` seconds into a `duration` clip. */
export function playheadX(time: number, duration: number, width: number): number {
  if (!(duration > 0) || !(width > 0)) return 0;
  return Math.min(1, Math.max(0, time / duration)) * width;
}

/** Root mean square of a block of samples. */
export function rms(block: ArrayLike<number>): number {
  if (block.length === 0) return 0;
  let s = 0;
  for (let i = 0; i < block.length; i++) s += block[i] * block[i];
  return Math.sqrt(s / block.length);
}

/** A loudness in 0..1 from an RMS value: -60 dBFS and below reads 0, full
 *  scale reads 1, linear in decibels between (how level meters move). */
export function levelOf(rmsValue: number, floorDb = -60): number {
  if (!(rmsValue > 0)) return 0;
  const db = 20 * Math.log10(rmsValue);
  return Math.min(1, Math.max(0, (db - floorDb) / -floorDb));
}

/** A short, stable id for a clip url, for the decode cache: the url itself when
 *  short, else its length plus a 32-bit FNV-1a hash of the whole string (a clip
 *  arrives as megabytes of base64; the key must not hold it twice). */
export function clipKey(url: string): string {
  if (url.length <= 200) return url;
  let h = 0x811c9dc5;
  for (let i = 0; i < url.length; i++) {
    h ^= url.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return `clip:${url.length}:${(h >>> 0).toString(16)}`;
}

/** Whether a media element playing `url` can be routed through Web Audio for a
 *  level meter. A cross-origin clip without CORS would play silent once routed,
 *  so only same-origin, data: and blob: clips are tapped. */
export function canTap(url: string, origin: string): boolean {
  const u = url.trim().toLowerCase();
  if (u.startsWith("data:") || u.startsWith("blob:")) return true;
  if (!/^https?:/.test(u)) return false;
  try {
    return new URL(url).origin === origin;
  } catch {
    return false;
  }
}

/** Whether a non-url audio value is encoded data (a long run of base64 with no
 *  spaces) rather than a line to speak: such a value is shown as unreadable
 *  audio, never read aloud character by character. */
export function looksLikeData(s: string): boolean {
  const t = s.trim();
  return t.length >= 64 && /^[A-Za-z0-9+/_-]+={0,2}$/.test(t);
}
