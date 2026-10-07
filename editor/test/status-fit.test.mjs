// ============================================================================
// Nothing in the status bar overlaps at any width. The latest line shrinks
// first: its message ellipsizes to a readable minimum, and answer buttons that
// do not fit beside that collapse into one "N choices" button that opens the
// console. Drives the REAL src/lib/statusFit.ts (transpiled with the installed
// TypeScript compiler) and reads StatusBar.tsx and editor.css, where the bar
// is laid out. Run from editor/: `node test/status-fit.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(resolve(here, rel), "utf8");
const js = ts.transpileModule(read("../src/lib/statusFit.ts"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { offerFits, choicesLabel } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// the kept-aside offer at 1440 / 1060 px (widths measured in the editor)
check("wide: the buttons fit beside the message minimum", offerFits(733, 85, 168, 392, 9), true);
check("1060 px: they collapse", offerFits(428, 85, 168, 392, 9), false);
check("exactly enough room fits", offerFits(85 + 9 + 168 + 9 + 392, 85, 168, 392, 9), true);
check("one pixel short collapses", offerFits(85 + 9 + 168 + 9 + 391, 85, 168, 392, 9), false);
check("the collapsed button counts the answers", [choicesLabel(1), choicesLabel(2)], ["1 choice", "2 choices"]);

const bar = read("../src/components/StatusBar.tsx");
check("the bar measures its buttons with offerFits", /offerFits\(el\.clientWidth, fixed, msgMin, measure\.offsetWidth, gap\)/.test(bar), true);
check("the collapsed button opens the console", /choicesLabel\(tail\.actions\.length\)/.test(bar) && /onClick=\{\(\) => setOpen\(true\)\}/.test(bar), true);
check("the counters keep their words in titles when they drop them", (bar.match(/className="tl"/g) || []).length, 4);

const css = read("../src/styles/editor.css");
check("the line clips inside its own box, never over the counters", /\.sb-tail\{[^}]*min-width:0;[^}]*overflow:hidden;/.test(css), true);
check("the counters and the right side keep their room", /\.sb-mid, \.sb-right\{flex:none; white-space:nowrap;\}/.test(css), true);
check("the message keeps a readable minimum", /\.sb-tail \.msg\{[^}]*text-overflow:ellipsis;[^}]*min-width:var\(--sb-msg-min\);/.test(css) && /--sb-msg-min:32ch;/.test(css), true);
check("collapsed, the message gives way so the one button is never cut", /\.sb-tail\.collapsed \.msg\{min-width:0;\}/.test(css), true);
check("the minimum is measured off a probe, not the message", /const msgMin = probe\.offsetWidth;/.test(bar), true);
check("a narrow bar sheds the cursor, then the words",
  /@container statusline \(max-width: 1180px\)\{ \.sb-right \.coord\.cursor\{display:none;\} \}/.test(css)
  && /@container statusline \(max-width: 940px\)\{ \.sb-mid \.tl, \.sb-right \.sb-btn \.tl\{display:none;\}/.test(css), true);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nstatus fit: all checks passed");
