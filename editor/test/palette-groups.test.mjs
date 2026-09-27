// ============================================================================
// Framework-free harness for the ⌘K palette's row order (src/lib/paletteGroups.ts):
// the flat list the arrow keys walk is the list as drawn, group by group in the
// fixed heading order, whatever order the caller passed its actions in. Drives
// the REAL module, transpiled with the installed TypeScript compiler. Run from
// editor/: `node test/palette-groups.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/paletteGroups.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { PALETTE_GROUPS, inGroupOrder } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const row = (group, key) => ({ group, key });
const keys = (rows) => rows.map((r) => r.key);

check("the drawn heading order", PALETTE_GROUPS, ["Add node", "Actions", "Help", "Go to node"]);

// a caller that lists a Help action first, and one between two ordinary actions
const passed = [
  row("Add node", "add:text"),
  row("Help", "act:help-docs"),
  row("Actions", "act:save"),
  row("Help", "act:help-bug"),
  row("Actions", "act:fit"),
  row("Go to node", "go:llm"),
];
const ordered = inGroupOrder(passed);
check("rows come out group by group in the drawn order",
  keys(ordered), ["add:text", "act:save", "act:fit", "act:help-docs", "act:help-bug", "go:llm"]);
check("each group keeps the order it came in",
  keys(ordered.filter((r) => r.group === "Help")), ["act:help-docs", "act:help-bug"]);
check("no row is lost or doubled", ordered.length, passed.length);

// the keyboard: ↓ from the last ordinary action lands on the first Help row,
// the row drawn right below it, and walks every group without jumping back.
const at = (key) => ordered.findIndex((r) => r.key === key);
check("down from the last action is the first Help row", ordered[at("act:fit") + 1].key, "act:help-docs");
const walk = ordered.map((r) => PALETTE_GROUPS.indexOf(r.group));
check("a full walk never goes back to an earlier group", walk.every((g, i) => i === 0 || g >= walk[i - 1]), true);

check("an empty list stays empty", inGroupOrder([]), []);
check("rows already in order are unchanged", keys(inGroupOrder(ordered)), keys(ordered));

if (failures) {
  console.log(`\n${failures} palette group check${failures === 1 ? "" : "s"} failed`);
  process.exit(1);
}
console.log("\nall palette group checks passed");
