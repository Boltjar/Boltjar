// ============================================================================
// Framework-free harness for how the canvas frames a graph it opens
// (src/lib/viewport.ts). Drives the REAL module, transpiled with the installed
// TypeScript compiler (it has no imports). Run from editor/: `node test/viewport.test.mjs`.
//
// The cases it guards: one React Flow instance serves every tab, so a new
// workflow opened after a large graph kept that graph's 25% zoom and its first
// node was tiny; an existing graph opened where the previous one was parked.
// Then: a graph of one or two nodes opened at 110% (the Fit button's cap), and
// every tab switch fitted the graph again, so a chat graph zoomed to 220% came
// back at 26% after a look at another tab. The last checks read Canvas.tsx and
// App.tsx, where the frame is applied and each open is counted.
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
const { EMPTY_VIEW, FIT_VIEW, OPEN_FIT, openingView, viewMemory } =
  await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ── the first open ──
check("a new, empty workflow opens at 100%", openingView(0), { kind: "set", viewport: { x: 0, y: 0, zoom: 1 } });
check("at the flow origin, whatever the last graph's pan", EMPTY_VIEW, { x: 0, y: 0, zoom: 1 });
check("a graph with nodes is fitted", openingView(1).kind, "fit");
check("a large graph is fitted the same way", openingView(200), { kind: "fit", options: OPEN_FIT });
check("an opened graph is fitted at most at 100%", OPEN_FIT.maxZoom, 1);
check("the Fit button may go to 110%", FIT_VIEW.maxZoom, 1.1);
check("both leave the same room around the graph", [OPEN_FIT.padding, FIT_VIEW.padding], [0.22, 0.22]);

// ── coming back to a graph ──
const views = viewMemory();
check("chat's first open is fitted", views.open("chat", 12).kind, "fit");
views.leave("chat", { x: -340, y: -120, zoom: 2.2 }); // zoomed in to 220%, then demo is opened
check("demo's first open is fitted", views.open("demo", 6).kind, "fit");
views.leave("demo", { x: 40, y: 10, zoom: 0.6 });
check("clicking the chat tab again brings chat back at 220%, where it was left",
  views.open("chat", 12), { kind: "set", viewport: { x: -340, y: -120, zoom: 2.2 } });
check("and demo where it was left", views.open("demo", 6), { kind: "set", viewport: { x: 40, y: 10, zoom: 0.6 } });
check("a graph not opened before in the session is still fitted", views.open("digest", 3).kind, "fit");
check("an empty one still at 100%", views.open("untitled-2", 0), { kind: "set", viewport: EMPTY_VIEW });
const kept = { x: 1, y: 2, zoom: 3 };
views.leave("copy", kept);
kept.zoom = 9; // the viewport object React Flow handed over changes later
check("a remembered view is a copy, not the live object", views.open("copy", 1).viewport.zoom, 3);

// ── the call sites ──
const canvas = readFileSync(resolve(here, "../src/components/canvas/Canvas.tsx"), "utf8");
check("Canvas frames through the view memory",
  /const viewsRef = useRef\(viewMemory\(\)\)/.test(canvas), true);
check("it remembers the graph it leaves before framing the next",
  /if \(left\) viewsRef\.current\.leave\(left\.slug, instance\.getViewport\(\)\);[\s\S]{0,160}viewsRef\.current\.open\(graphName, nodes\.length\)/.test(canvas), true);
check("it frames once per viewKey, after the graph has loaded",
  /if \(!instance \|\| loading \|\| framedRef\.current\?\.key === viewKey\) return;/.test(canvas), true);
check("the Fit button uses FIT_VIEW", /rf\.fitView\(\{ \.\.\.FIT_VIEW, duration: 300 \}\)/.test(canvas), true);
const app = readFileSync(resolve(here, "../src/App.tsx"), "utf8");
check("App's show() counts each graph it puts on the canvas",
  /const show = \(g: Graph, opts\?: \{ dirty\?: boolean \}\) => \{[\s\S]{0,200}setOpenedGraphs\(\(n\) => n \+ 1\);/.test(app), true);
check("and hands the count to the canvas as viewKey", /viewKey=\{openedGraphs\}/.test(app), true);
check("the load path asks graphSource before any fetch",
  /const source = graphSource\(tabsState, target, draft\?\.graph \?\? null\);[\s\S]{0,1000}const loaded = await fetchServerGraph\(target\);/.test(app), true);

if (failures) {
  console.error(`\n${failures} viewport check(s) failed`);
  process.exit(1);
}
console.log("\nall viewport checks passed");
