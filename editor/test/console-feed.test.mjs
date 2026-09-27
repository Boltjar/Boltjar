// ============================================================================
// Framework-free checks of the console panel's feed (src/lib/consoleFeed.ts):
// which event kinds it shows by default, how identical lines collapse, and
// the bound on each stream. Drives the REAL module, transpiled with the
// installed TypeScript compiler (it has no imports), then checks that the
// socket hook and the panel use it. Run from editor/: `node test/console-feed.test.mjs`.
//
// The case it guards: with an Interval firing every second the console printed
// every value of every node every second ("interval trigger = 72", "preview
// out = ..."), so the lines worth reading were buried and the panel flickered.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const read = (path) => readFileSync(resolve(here, path), "utf8");
const js = ts.transpileModule(read("../src/lib/consoleFeed.ts"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { streamOf, appendLine, visibleEntries, latestEntry, EMPTY_FEED, CONSOLE_KEEP } =
  await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const line = (kind, message, extra = {}) => ({ kind, ts: "00:00:00", level: "info", message, ...extra });
const feedOf = (lines) => lines.reduce((f, l) => appendLine(f, l), EMPTY_FEED);
const shown = (f, values = false) => visibleEntries(f, values).map((e) => (e.count > 1 ? `${e.message} ×${e.count}` : e.message));

// ---- which kinds show by default
check("a port's value is in the values stream", streamOf("value"), "values");
check("a model's tool call and result are in the values stream", [streamOf("tool_call"), streamOf("tool_result")], ["values", "values"]);
for (const kind of ["log", "warning", "node_error", "error", "invalid", "status", "notice"]) {
  check(`a ${kind} line shows by default`, streamOf(kind), "readable");
}

// ---- an Interval at 1 s feeding a Preview and two Logs, for a minute: the
// Preview and the "tick" Log print the same text each second, "count" does not
{
  let f = EMPTY_FEED;
  f = appendLine(f, line("status", "power on: graph is live", { level: "ok" }));
  for (let i = 1; i <= 60; i += 1) {
    const ts = `00:01:${String(i).padStart(2, "0")}`;
    f = appendLine(f, line("value", `trigger = ${i}`, { node: "interval", tag: "trigger" }));
    f = appendLine(f, line("value", "out = same", { node: "preview", tag: "out" }));
    f = appendLine(f, line("value", "trigger = true", { node: "preview", tag: "trigger" }));
    f = appendLine(f, line("log", "preview: same", { node: "preview", level: "ok", ts }));
    f = appendLine(f, line("log", `count: ${i}`, { node: "count", level: "ok", ts }));
    f = appendLine(f, line("log", "tick: same", { node: "log", level: "ok", ts }));
  }
  const view = shown(f);
  check("by default no value is shown", view.some((m) => m.includes(" = ")), false);
  check("the repeating lines are one line each, counted", view.filter((m) => m.includes("same")), ["preview: same ×60", "tick: same ×60"]);
  check("a changing line is a new entry each time", view.filter((m) => m.startsWith("count:")).length, 60);
  check("a counted line stays where it began", view.slice(0, 5), ["power on: graph is live", "preview: same ×60", "count: 1", "tick: same ×60", "count: 2"]);
  check("the counted line carries the latest time", visibleEntries(f, false)[3].ts, "00:01:60");
  check("the status line shows the entry changed last", latestEntry(visibleEntries(f, false)).message, "tick: same");
  const values = visibleEntries(f, true).filter((e) => e.stream === "values");
  check("values on adds the value stream", values.length, f.values.length);
  check("a value repeating on its port is counted too",
    values.filter((e) => e.node === "preview").map((e) => `${e.message} ×${e.count}`).sort(), ["out = same ×60", "trigger = true ×60"]);
  check("a changing value is a new entry each time", values.filter((e) => e.node === "interval").length, 60);
}

// ---- only the changed entry is a new object; values leave the readable view alone
{
  const f1 = feedOf([line("log", "a"), line("log", "b")]);
  const f2 = appendLine(f1, line("log", "b"));
  check("a collapse keeps the other entries as they were", f2.readable[0] === f1.readable[0], true);
  check("and keeps the entry's id (its row)", f2.readable[1].id, f1.readable[1].id);
  const g1 = feedOf([line("log", "same", { node: "a" }), line("log", "x", { node: "b" })]);
  const g2 = appendLine(g1, line("log", "same", { node: "a" }));
  check("no entry moves when an earlier one counts", g2.readable.map((e) => e.id), g1.readable.map((e) => e.id));
  check("and the others keep their objects", g2.readable[1] === g1.readable[1], true);
  const f3 = appendLine(f2, line("value", "x = 1"));
  check("a value leaves the default view the same array", visibleEntries(f3, false) === visibleEntries(f2, false), true);
}

// ---- the bound, per stream
{
  let f = appendLine(EMPTY_FEED, line("error", "boom", { level: "bad" }));
  for (let i = 0; i < CONSOLE_KEEP + 200; i += 1) f = appendLine(f, line("value", `v = ${i}`));
  check("each stream keeps its last CONSOLE_KEEP entries", f.values.length, CONSOLE_KEEP);
  check("the oldest values go first", f.values[0].message, "v = 200");
  check("a flood of values never pushes an error out", shown(f), ["boom"]);
  let g = EMPTY_FEED;
  for (let i = 0; i < CONSOLE_KEEP + 5; i += 1) g = appendLine(g, line("log", `l${i}`));
  check("the readable stream is bounded too", [g.readable.length, g.readable[0].message], [CONSOLE_KEEP, "l5"]);
  check("CONSOLE_KEEP is 500", CONSOLE_KEEP, 500);
}

// ---- the hook and the panel use it
const hook = read("../src/hooks/useRunSocket.ts");
check("the socket hook feeds the console through appendLine", /appendLine\(/.test(hook), true);
check("every value still counts toward ev/s", /case "value": \{[\s\S]*?eventTimes\.current\.push\(t\)/.test(hook), true);
const bar = read("../src/components/StatusBar.tsx");
check("the panel shows visibleEntries", /visibleEntries\(/.test(bar), true);
check("the values toggle is a console chip", /className=\{`chip \$\{showValues \? "on" : ""\}`\}/.test(bar), true);

if (failures) {
  console.error(`\n${failures} failing check(s)`);
  process.exit(1);
}
console.log("\nall console feed checks passed");
