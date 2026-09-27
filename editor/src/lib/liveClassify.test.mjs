// ============================================================================
// liveClassify.test.mjs: framework-free regression test for the draft-vs-live
// classification (the fix for: a Preview wired onto a live source while the graph
// is ON must NOT show live data until Save & Restart).
//
// No test runner is installed, so this transpiles liveClassify.ts with the
// already-present esbuild and asserts with node:assert. Run from editor/:
//   node src/lib/liveClassify.test.mjs
// Exits non-zero on any failure.
// ============================================================================
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { transformSync } from "esbuild";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "liveClassify.ts"), "utf8");
const { code } = transformSync(src, { loader: "ts", format: "esm" });
const mod = await import(`data:text/javascript,${encodeURIComponent(code)}`);
const { liveEdgeKey, isEdgeLive, liveGatePasses } = mod;

let passed = 0;
const ok = (name, fn) => { fn(); passed++; console.log(`  ok  ${name}`); };

// ── liveEdgeKey must match graphAdapter's edgeId format exactly ──
ok("liveEdgeKey matches the src:port->dst:port edgeId format", () => {
  assert.equal(liveEdgeKey("m", "trigger", "log", "in"), "m:trigger->log:in");
});

// ── isEdgeLive is plain set membership ──
ok("isEdgeLive: a wire in the live set is live, one not in it is draft", () => {
  const live = new Set(["m:trigger->log:in"]);
  assert.equal(isEdgeLive(live, "m:trigger->log:in"), true);
  // a Preview wire drawn off the same live source AFTER power-on is absent:
  assert.equal(isEdgeLive(live, "m:trigger->preview1:in"), false);
});

// ── the gate: the exact reported bug ──
ok("gate: while ON a draft wire is suppressed (Save & Restart to tap)", () => {
  assert.equal(liveGatePasses("on", false), false); // THE BUG, now suppressed
});
ok("gate: while ON a live wire shows live data", () => {
  assert.equal(liveGatePasses("on", true), true);
});
ok("gate: while OFF everything shows last-captured values (draft or not)", () => {
  assert.equal(liveGatePasses("off", false), true);
  assert.equal(liveGatePasses("off", true), true);
});

// ── end-to-end: source stays live, a fresh Preview wire is draft ──
ok("scenario: new Preview onto a live source is draft while ON, live after restart", () => {
  const beforeRestart = new Set(["m:trigger->log:in"]);          // running graph
  const newWire = liveEdgeKey("m", "trigger", "preview1", "in"); // structural edit
  // ON + before Save&Restart: the new wire is NOT in the live set -> suppressed.
  assert.equal(liveGatePasses("on", isEdgeLive(beforeRestart, newWire)), false);
  // after Save & Restart the server includes it -> it shows live.
  const afterRestart = new Set([...beforeRestart, newWire]);
  assert.equal(liveGatePasses("on", isEdgeLive(afterRestart, newWire)), true);
});

console.log(`\n${passed} passed`);
