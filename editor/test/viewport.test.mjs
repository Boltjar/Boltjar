// ============================================================================
// Framework-free harness for how the canvas frames a graph it opens
// (src/lib/viewport.ts). Drives the REAL module, transpiled with the installed
// TypeScript compiler (it has no imports). Run from editor/: `node test/viewport.test.mjs`.
//
// The case it guards: one React Flow instance serves every tab, so a new
// workflow opened after a large graph kept that graph's 25% zoom and its first
// node was tiny; an existing graph opened where the previous one was parked.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/viewport.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { EMPTY_VIEW, FIT_VIEW, openingView } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

check("a new, empty workflow opens at 100%", openingView(0), { kind: "set", viewport: { x: 0, y: 0, zoom: 1 } });
check("at the flow origin, whatever the last graph's pan", EMPTY_VIEW, { x: 0, y: 0, zoom: 1 });
check("a graph with nodes is fitted", openingView(1).kind, "fit");
check("a large graph is fitted the same way", openingView(200), { kind: "fit", options: FIT_VIEW });
check("a fit never zooms past 110%", FIT_VIEW.maxZoom, 1.1);
check("a fit leaves room around the graph", FIT_VIEW.padding, 0.22);

if (failures) {
  console.error(`\n${failures} viewport check(s) failed`);
  process.exit(1);
}
console.log("\nall viewport checks passed");
