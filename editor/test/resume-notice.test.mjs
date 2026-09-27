// ============================================================================
// Framework-free checks of a graph the launch could not turn back On
// (src/lib/resumeNotice.ts). The server retries it at every launch and replays
// why it failed to each editor that opens it; the graph is Off, so the power
// toggle offers no Off, and "Stop resuming" must be there instead: in the
// command palette, and in the Problems panel when it lists the problems.
// Drives the REAL module, transpiled with the installed TypeScript compiler
// (it has no imports), then checks the socket hook, App and the panel use it.
// Run from editor/: `node test/resume-notice.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const read = (path) => readFileSync(resolve(here, path), "utf8");
const js = ts.transpileModule(read("../src/lib/resumeNotice.ts"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { resumeNoticeAfter, STOP_RESUMING } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

function after(events, showing = false) {
  return events.reduce((s, evt) => resumeNoticeAfter(s, evt), showing);
}

// ---- when the notice shows
const connect = [{ kind: "status", power: "off" }, { kind: "live_graph", nodes: [], edges: [] }];
check("an editor opening a graph the launch could not resume shows it",
  after([...connect, { kind: "invalid", problems: [], resume: true }]), true);
check("a build that failed at the launch shows it too",
  after([...connect, { kind: "error", error: "RuntimeError('locked')", resume: true }]), true);
check("a person's own refused On is not a launch's notice",
  after([...connect, { kind: "invalid", problems: [] }]), false);
check("a later refusal of the person's own leaves the launch's notice up",
  after([{ kind: "invalid", problems: [] }], true), true);
check("the graph turning On ends it", after([{ kind: "status", power: "on" }], true), false);
check("a person's Off ends it", after([{ kind: "status", power: "off" }], true), false);
check("values and logs leave it as it is",
  after([{ kind: "value", node: "a", port: "b", value: 1 }, { kind: "log", node: "a", message: "x" }], true),
  true);

// ---- its copy
check("the action says what it does", STOP_RESUMING.label, "Stop resuming this graph");
check("no copy holds an em dash",
  Object.values(STOP_RESUMING).some((text) => text.includes(String.fromCharCode(0x2014))), false);
const iconSet = read("../src/lib/icons.tsx");
check("its icon is one the icon set has", iconSet.includes(`"${STOP_RESUMING.icon}":`), true);

// ---- the wiring
const hook = read("../src/hooks/useRunSocket.ts");
check("the socket hook follows every event through resumeNoticeAfter",
  /setResumeNotice\(\(showing\) => resumeNoticeAfter\(showing, evt\)\)/.test(hook), true);
check("a tab switch clears it", /setResumeNotice\(false\)/.test(hook), true);
const app = read("../src/App.tsx");
check("the palette offers Stop resuming while the notice shows and the graph is Off",
  /socket\.resumeNotice && socket\.power !== "on"\s*\?\s*\[\{ id: "stop-resuming"/.test(app), true);
check("Stop resuming sends a person's Off", /id: "stop-resuming"[^\n]*run: stopResuming/.test(app)
  && /const stopResuming = useCallback\(\(\) => socket\.off\(\)/.test(app), true);
check("the Problems panel gets the action while the notice shows",
  /onStopResuming=\{socket\.resumeNotice \? stopResuming : undefined\}/.test(app), true);
const panel = read("../src/components/ProblemsPanel.tsx");
check("the Problems panel draws it with the shared action button",
  /onStopResuming && \([\s\S]*?className="conn-action-btn"[\s\S]*?onClick=\{onStopResuming\}/.test(panel), true);

if (failures) {
  console.error(`\n${failures} resume notice check(s) failed`);
  process.exit(1);
}
console.log("\nall resume notice checks passed");
