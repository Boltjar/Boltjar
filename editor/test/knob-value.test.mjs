// ============================================================================
// Framework-free checks that a knob never hides its own value (the Schedule's
// "cron (min hour day month weekday)" label cut its value to "0 9 *"). Drives
// the REAL valueChars (src/lib/knobOptions.ts, transpiled with the installed
// TypeScript compiler) and reads the Knob component and its styles, where the
// row wraps the value under the label when the two do not fit.
// Run from editor/: `node test/knob-value.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(resolve(here, rel), "utf8");
const js = ts.transpileModule(read("../src/lib/knobOptions.ts"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { valueChars } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ── the width a value asks for ──
check("a cron value asks for all nine characters", valueChars("0 9 * * *"), 9);
check("a short value asks for the minimum", valueChars("ab"), 4);
check("an empty value keeps room to type", valueChars(""), 4);
check("a missing value keeps room to type", valueChars(undefined), 4);
check("a number counts its digits", valueChars(86400, 2), 5);
check("the minimum is the caller's", valueChars(1, 2), 2);

// ── the Knob asks for it and marks a wrapped row ──
const knob = read("../src/components/canvas/Knob.tsx");
check("the text input asks for its value's width", /--vlen[\s\S]{0,40}valueChars\(textDraft\)/.test(knob), true);
check("the number input asks for its value's width", /--vlen[\s\S]{0,40}valueChars\(Number\.isFinite\(num\)/.test(knob), true);
check("text, select and number rows know when they wrapped",
  (knob.match(/ref=\{rowRef\}/g) || []).length, 3);
check("a wrapped row is marked stacked", /stacked \? " stacked" : ""/.test(knob), true);

// ── the styles wrap the value and align it left once it wrapped ──
const css = read("../src/styles/editor.css");
const cssRule = (sel) => {
  const i = css.lastIndexOf(sel + "{");
  return i < 0 ? "" : css.slice(i, css.indexOf("}", i) + 1);
};
check("the rows wrap", cssRule(".knob.text, .knob-numrow, .knob.select").includes("flex-wrap:wrap"), true);
check("the text value's width comes from --vlen", cssRule(".knob.text input").includes("var(--vlen"), true);
check("the number value's width comes from --vlen", cssRule(".knob-numrow input").includes("var(--vlen"), true);
check("a wrapped value aligns left",
  cssRule(".knob.text.stacked input, .knob-numrow.stacked input").includes("text-align:left"), true);
check("the wrap rule comes after the row rules it changes (their gap would win)",
  css.lastIndexOf(".knob.text, .knob-numrow, .knob.select{") > css.lastIndexOf(".knob.select{display:flex"), true);
check("a label longer than its row wraps inside it", /(^|\n)\.knob-lbl\{[^}]*max-width:100%/.test(css), true);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nknob value: all checks passed");
