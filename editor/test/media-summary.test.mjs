// ============================================================================
// Framework-free harness for the console's media summary (src/lib/mediaSummary.ts):
// a data: URL shows as "mime · size", a media link as "kind · link", long text
// as its head plus the total length, and no base64 payload ever reaches a
// console line. Drives the REAL module, transpiled with the installed
// TypeScript compiler. Run from editor/: `node test/media-summary.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/mediaSummary.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { mediaSummary, summarizeDataUrls, consoleText, formatBytes } =
  await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// a base64 payload that decodes to exactly `bytes` bytes, with its padding.
function b64(bytes) {
  const full = Math.floor(bytes / 3) * 4;
  const rest = bytes % 3;
  return "A".repeat(full) + (rest === 0 ? "" : rest === 1 ? "AA==" : "AAA=");
}

// ---- sizes
check("bytes under a KB stay in bytes", formatBytes(512), "512 B");
check("a KB and a half", formatBytes(1536), "1.5 KB");
check("whole KB drop the decimal", formatBytes(1024), "1 KB");
check("tens of KB round", formatBytes(43008), "42 KB");
check("megabytes", formatBytes(3.2 * 1024 * 1024), "3.2 MB");

// ---- a value that IS a data: URL
check("wav data URL", mediaSummary(`data:audio/wav;base64,${b64(43008)}`), "audio/wav · 42 KB");
check("padding does not count as bytes", mediaSummary(`data:image/png;base64,${b64(4)}`), "image/png · 4 B");
check("two padding chars", mediaSummary(`data:image/png;base64,${b64(5)}`), "image/png · 5 B");
check("params before base64", mediaSummary("data:audio/webm;codecs=opus;base64,AAAA"), "audio/webm · 3 B");
check("no mime is text/plain, percent escapes are one byte",
  mediaSummary("data:,Hello%2C%20World"), "text/plain · 12 B");
check("mime is lowercased", mediaSummary("data:Audio/MPEG;base64,AAAA"), "audio/mpeg · 3 B");
check("surrounding whitespace is ignored", mediaSummary(`  data:audio/wav;base64,${b64(3)}\n`), "audio/wav · 3 B");

// ---- links
check("blob URL", mediaSummary("blob:http://127.0.0.1:8770/5f1c7a2e-0b8e-4c55-9a51-3f4f1b2d9e10"), "media · link");
check("https audio file", mediaSummary("https://example.com/clips/hello.mp3?sig=abc"), "audio · link");
check("http image file with a fragment", mediaSummary("http://example.com/a/b.PNG#top"), "image · link");
check("https video file", mediaSummary("https://example.com/v.webm"), "video · link");
check("a page link is not media", mediaSummary("https://example.com/docs"), null);
check("a link inside a sentence is not a media value", mediaSummary("see https://example.com/a.mp3 now"), null);
check("plain text is not media", mediaSummary("hello"), null);

// ---- data: URLs inside a larger text
check("a log line with a data URL",
  summarizeDataUrls(`-> chat: data:audio/wav;base64,${b64(6)}`), "-> chat: audio/wav · 6 B");
check("two data URLs in JSON",
  summarizeDataUrls(JSON.stringify({ a: `data:image/png;base64,${b64(3)}`, b: `data:audio/mpeg;base64,${b64(2048)}` })),
  '{"a":"image/png · 3 B","b":"audio/mpeg · 2 KB"}');
check("metadata: is not a data URL", summarizeDataUrls("metadata:,x"), "metadata:,x");

// ---- console lines
check("short text passes through", consoleText("hello there"), "hello there");
check("whitespace collapses to one line", consoleText("  line one\n\n  line two\t"), "line one line two");
check("a number", consoleText(42), "42");
check("null", consoleText(null), "null");
check("undefined", consoleText(undefined), "undefined");
check("an object is JSON", consoleText({ ok: true }), '{"ok":true}');
check("a data URL value", consoleText(`data:audio/wav;base64,${b64(43008)}`), "audio/wav · 42 KB");
check("a media link value", consoleText("https://example.com/x.wav"), "audio · link");
check("long text keeps its head and total length",
  consoleText("a".repeat(100), 60), `${"a".repeat(59)}… · 100 chars`);
check("the total length groups thousands",
  consoleText("b".repeat(12345), 10), `${"b".repeat(9)}… · 12,345 chars`);
check("text exactly at the limit is whole", consoleText("c".repeat(60), 60), "c".repeat(60));
check("a JSON value with a data URL summarizes before cutting",
  consoleText({ audio: `data:audio/wav;base64,${b64(3000)}`, text: "hi" }, 200),
  '{"audio":"audio/wav · 2.9 KB","text":"hi"}');

// ---- the guarantee: a big base64 payload never reaches the console
const big = `data:audio/wav;base64,${b64(2 * 1024 * 1024)}`;
const lines = [
  consoleText(big),
  consoleText({ reply: "ok", audio: big }, 500),
  consoleText(`tool result -> ${JSON.stringify({ audio: big })}`, 500),
  consoleText(`-> chat: ${big}`, 500),
];
check("no console line carries the payload", lines.every((l) => !l.includes("AAAAAAAA") && l.length < 120), true);
check("the payload shows as its size", lines[0], "audio/wav · 2 MB");

if (failures) {
  console.log(`\n${failures} media summary check${failures === 1 ? "" : "s"} failed`);
  process.exit(1);
}
console.log("\nall media summary checks passed");
