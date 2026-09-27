// ============================================================================
// Framework-free harness for the pan hint's place on the canvas's bottom row
// (src/lib/panHint.ts). Drives the REAL module, transpiled with the installed
// TypeScript compiler (it has no imports). Run from editor/: `node test/pan-hint.test.mjs`.
//
// The case it guards: on a canvas about 545 px wide the centred "drag to pan ·
// scroll to zoom" pill lay over the zoom buttons and the minimap. The row is
// measured the way the canvas lays it out (editor.css): the credit ends 78 px
// from the left, the zoom cluster starts 16 + 202 + 12 + 30 = 260 px from the
// right, the pill is 223 px wide, and the gap and inset are the --space-3 and
// --space-4 tokens (12 and 16 px).
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
async function load(rel) {
  const src = readFileSync(resolve(here, "../src/lib", rel), "utf8");
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  return import("data:text/javascript," + encodeURIComponent(js));
}
const { panHintCenter } = await load("panHint.ts");

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const HINT = 223;
const row = (width, extra = {}) => ({
  width, hint: HINT, left: 78, right: width - 260, gap: 12, inset: 16, ...extra,
});
/** The pill's span on the row, or null when it is hidden. */
function span(width, extra) {
  const r = row(width, extra);
  const x = panHintCenter(r);
  return x === null ? null : { from: x - HINT / 2, to: x + HINT / 2, r };
}

check("a wide canvas keeps the pill centred", panHintCenter(row(1400)), 700);
check("the pill stays centred while the centre is clear", panHintCenter(row(800)), 400);
check("a narrower canvas slides the pill left, clear of the zoom cluster",
  panHintCenter(row(700)), 700 - 260 - 12 - HINT / 2);
check("the reported 545 px canvas has no room: the pill hides", panHintCenter(row(545)), null);
check("no credit: the canvas inset is the left bound",
  panHintCenter(row(560, { left: 0 })), 560 - 260 - 12 - HINT / 2);
check("a pill not measured yet (0 wide) stays hidden", panHintCenter(row(1400, { hint: 0 })), null);

// every width from a phone to a wide monitor: the pill, when shown, never
// crosses the credit or the zoom cluster (and so never the minimap beyond it).
let overlaps = 0;
let shown = 0;
for (let width = 200; width <= 2400; width += 1) {
  const s = span(width);
  if (!s) continue;
  shown += 1;
  if (s.from < s.r.left + s.r.gap - 1e-9 || s.from < s.r.inset - 1e-9 || s.to > s.r.right - s.r.gap + 1e-9) overlaps += 1;
}
check("no width puts the pill over its neighbours", overlaps, 0);
check("the pill still shows on most widths", shown > 1800, true);
check("the pill shows from the first width with room",
  span(78 + 12 + HINT + 12 + 260) !== null && span(78 + 12 + HINT + 12 + 259) === null, true);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall pan hint checks passed");
