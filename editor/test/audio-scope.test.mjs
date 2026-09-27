// ============================================================================
// Framework-free harness for the AudioScope maths (src/lib/audioScope.ts): the
// min/max envelope per pixel column, the mono mixdown, re-reducing at a new
// width, the amplitude scale, clock and time-axis labels, seeking from x, the
// playhead x from a time, the level meter and the tap rule. Drives the REAL
// module, transpiled with the installed TypeScript compiler. Run from editor/:
// `node test/audio-scope.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/audioScope.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const {
  mixdown, envelope, reduceEnvelope, peakOf, ampScale, formatAmp, formatClock,
  tickStep, timeLabels, seekTime, playheadX, rms, levelOf, clipKey, canTap, looksLikeData, CLIP_BINS,
} = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}
const arr = (f32) => Array.from(f32, (v) => Math.round(v * 1000) / 1000);
const f32 = (xs) => Float32Array.from(xs);

// ---- mono mixdown
check("one channel passes through", arr(mixdown([f32([0.5, -0.5, 1])])), [0.5, -0.5, 1]);
check("two channels average", arr(mixdown([f32([1, 0, -1]), f32([0, 0, -1])])), [0.5, 0, -1]);
check("opposite channels cancel", arr(mixdown([f32([0.8, -0.8]), f32([-0.8, 0.8])])), [0, 0]);
check("unequal channels mix over the shortest", arr(mixdown([f32([1, 1, 1]), f32([1, 1])])), [1, 1]);
check("no channels is empty", mixdown([]).length, 0);
check("mixdown copies (never aliases the decoder's buffer)", (() => {
  const c = f32([0.1]);
  const m = mixdown([c]);
  m[0] = 9;
  return c[0] !== 9;
})(), true);

// ---- min/max per column
const ramp = f32([0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]);
check("8 samples in 4 columns: min per column", arr(envelope(ramp, 4).min), [0, 0.2, 0.4, 0.6]);
check("8 samples in 4 columns: max per column", arr(envelope(ramp, 4).max), [0.1, 0.3, 0.5, 0.7]);
const wave = f32([0.9, -0.9, 0.2, -0.1, 0, 0, 0.5, -0.4, 0.3]);
check("a column keeps its extremes", arr(envelope(wave, 3).min), [-0.9, -0.1, -0.4]);
check("a column keeps its extremes (max)", arr(envelope(wave, 3).max), [0.9, 0, 0.5]);
check("uneven split still covers every sample", (() => {
  const s = f32([1, 2, 3, 4, 5, 6, 7]);
  const e = envelope(s, 3);
  return [arr(e.min), arr(e.max)];
})(), [[1, 3, 5], [2, 4, 7]]);
check("more columns than samples repeats the nearest", arr(envelope(f32([0.5, -0.5]), 4).max), [0.5, 0.5, -0.5, -0.5]);
check("zero columns is empty", envelope(ramp, 0).min.length, 0);
check("no samples gives flat zero columns", arr(envelope(f32([]), 3).max), [0, 0, 0]);
check("fractional column count floors", envelope(ramp, 4.9).min.length, 4);

// ---- width changes re-reduce the cached bins, never the samples
const long = new Float32Array(10000);
for (let i = 0; i < long.length; i++) long[i] = Math.sin(i / 7) * (i < 5000 ? 0.3 : 0.9);
const bins = envelope(long, 1000);
const direct = envelope(long, 250);
const viaBins = reduceEnvelope(bins, 250);
check("reducing the bins equals reducing the samples (max)", arr(viaBins.max), arr(direct.max));
check("reducing the bins equals reducing the samples (min)", arr(viaBins.min), arr(direct.min));
check("a narrower width has fewer columns", reduceEnvelope(bins, 120).max.length, 120);
check("the quiet half stays quiet at any width", Math.max(...reduceEnvelope(bins, 40).max.slice(0, 20)) <= 0.3 + 1e-6, true);
check("the loud half stays loud at any width", Math.max(...reduceEnvelope(bins, 40).max.slice(20)) > 0.85, true);
check("clip bins are a fixed 4096", CLIP_BINS, 4096);

// ---- peak and amplitude scale
check("peak takes the larger side", Math.round(peakOf({ min: f32([-0.7]), max: f32([0.4]) }) * 100) / 100, 0.7);
check("silence has no peak", peakOf(envelope(f32([0, 0]), 2)), 0);
check("silence draws at full scale", ampScale(0), 1);
check("a quiet clip scales to 0.1", ampScale(0.06), 0.1);
check("a speech-level clip scales to 0.25", ampScale(0.21), 0.25);
check("0.5 holds exactly 0.5", ampScale(0.5), 0.5);
check("a loud clip is full scale", ampScale(0.93), 1);
check("a clipping clip stays full scale", ampScale(1.4), 1);
check("amp label +1", formatAmp(1), "+1");
check("amp label -1", formatAmp(-1), "-1");
check("amp label +0.25", formatAmp(0.25), "+0.25");
check("amp label -0.5", formatAmp(-0.5), "-0.5");

// ---- clock
check("zero", formatClock(0), "0:00");
check("whole seconds", formatClock(3), "0:03");
check("duration with a decimal", formatClock(3.14, 1), "0:03.1");
check("rounds up into the next minute", formatClock(59.96, 1), "1:00.0");
check("minutes", formatClock(62, 0), "1:02");
check("long clip", formatClock(725.25, 1), "12:05.3");
check("two decimals", formatClock(0.25, 2), "0:00.25");
check("negative clamps to zero", formatClock(-2), "0:00");
check("NaN reads zero", formatClock(NaN, 1), "0:00.0");

// ---- tick step and time labels
check("3 s on 300 px ticks every second", tickStep(3, 300), 1);
check("3 s on 100 px ticks every 2 s", tickStep(3, 100), 2);
check("0.8 s on 300 px ticks every 0.2 s", tickStep(0.8, 300), 0.2);
check("a 10 min clip on 280 px ticks every 5 min", tickStep(600, 280), 300);
check("a tighter gap allows denser ticks", tickStep(3, 300, 48), 0.5);
check("no duration, no step", tickStep(0, 300), 0);
const lab = timeLabels(3.1, 300, 6);
check("labels: first tick starts at 0:00", lab[0], { x: 0, text: "0:00", align: "start" });
check("labels: the duration ends at the right edge", lab[lab.length - 1], { x: 300, text: "0:03.1", align: "end", end: true });
check("labels: whole-second ticks between", lab.slice(1, -1).map((l) => l.text), ["0:01", "0:02"]);
check("labels: a tick under the duration label is dropped", timeLabels(3.02, 300, 6).map((l) => l.text), ["0:00", "0:01", "0:02", "0:03.0"]);
check("labels: sub-second ticks carry a decimal", timeLabels(0.8, 300, 6).map((l) => l.text), ["0:00.0", "0:00.2", "0:00.4", "0:00.6", "0:00.8"]);
check("labels: no neighbour overlaps", (() => {
  const ls = timeLabels(47.3, 290, 6.2);
  let right = -Infinity;
  for (const l of ls) {
    const w = l.text.length * 6.2;
    const left = l.align === "start" ? l.x : l.align === "end" ? l.x - w : l.x - w / 2;
    if (left < right) return false;
    right = left + w;
  }
  return ls.length >= 3 && right <= 290 + 1e-9;
})(), true);
check("labels: nothing without a duration", timeLabels(0, 300, 6), []);

// ---- seek and playhead
check("seek at the left edge", seekTime(0, 300, 3.1), 0);
check("seek at the middle", seekTime(150, 300, 3), 1.5);
check("seek at the right edge", seekTime(300, 300, 3), 3);
check("seek left of the scope clamps", seekTime(-20, 300, 3), 0);
check("seek right of the scope clamps", seekTime(340, 300, 3), 3);
check("seek with no duration", seekTime(100, 300, 0), 0);
check("seek is width-unit agnostic (a zoomed node)", seekTime(75, 150, 4), 2);
check("playhead at the start", playheadX(0, 3, 300), 0);
check("playhead a third in", playheadX(1, 3, 300), 100);
check("playhead past the end clamps", playheadX(9, 3, 300), 300);
check("playhead with no duration", playheadX(1, 0, 300), 0);
check("seek then playhead round-trips", Math.round(playheadX(seekTime(123, 300, 2.7), 2.7, 300) * 1e6) / 1e6, 123);

// ---- level meter
check("rms of silence", rms(f32([0, 0, 0])), 0);
check("rms of a full-scale square", rms(f32([1, -1, 1, -1])), 1);
check("rms of nothing", rms(f32([])), 0);
check("full scale reads 1", levelOf(1), 1);
check("-60 dBFS reads 0", levelOf(0.001), 0);
check("-30 dBFS reads half", Math.round(levelOf(10 ** (-30 / 20)) * 1000) / 1000, 0.5);
check("silence reads 0", levelOf(0), 0);

// ---- cache key
check("a short url is its own key", clipKey("https://example.com/a.wav"), "https://example.com/a.wav");
const clipA = "data:audio/wav;base64," + "A".repeat(5000) + "B";
const clipB = "data:audio/wav;base64," + "A".repeat(5000) + "C";
check("a long clip keys by length and hash", /^clip:5023:[0-9a-f]+$/.test(clipKey(clipA)), true);
check("the same clip keys the same", clipKey(clipA) === clipKey(String(clipA)), true);
check("clips one byte apart key apart", clipKey(clipA) !== clipKey(clipB), true);

// ---- which clips may be routed through Web Audio
check("data: clip is tapped", canTap("data:audio/wav;base64,AAAA", "http://127.0.0.1:8770"), true);
check("blob: clip is tapped", canTap("blob:http://127.0.0.1:8770/x", "http://127.0.0.1:8770"), true);
check("same-origin clip is tapped", canTap("http://127.0.0.1:8770/files/a.wav", "http://127.0.0.1:8770"), true);
check("cross-origin clip is never tapped (it would play silent)", canTap("https://cdn.example.com/a.wav", "http://127.0.0.1:8770"), false);
check("plain text is not a clip", canTap("hello", "http://127.0.0.1:8770"), false);

// ---- a non-url value: a line to speak, or encoded data
check("a sentence is speech", looksLikeData("Hello there, how are you today? This line is long enough to count."), false);
check("a short word is speech", looksLikeData("hello"), false);
check("raw base64 PCM is data", looksLikeData("UklGR".repeat(20) + "=="), true);
check("url-safe base64 is data", looksLikeData("A-_b".repeat(20)), true);

if (failures) {
  console.error(`\n${failures} audio-scope check(s) failed`);
  process.exit(1);
}
console.log("\nall audio-scope checks passed");
