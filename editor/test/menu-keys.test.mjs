// ============================================================================
// Framework-free harness for ContextMenu keyboard focus (src/lib/menuKeys.ts):
// ↑/↓ walk the enabled items and wrap, Home/End jump to the ends, and a menu
// with a node search keeps its search box as the stop after the last item.
// Drives the REAL module, transpiled with the installed TypeScript compiler.
// Run from editor/: `node test/menu-keys.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/menuKeys.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { menuFocusTarget } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- a plain menu of 6 items (the Help menu)
check("down moves to the next item", menuFocusTarget("ArrowDown", 0, 6, false), 1);
check("up moves to the item above", menuFocusTarget("ArrowUp", 3, 6, false), 2);
check("down from the last item wraps to the first", menuFocusTarget("ArrowDown", 5, 6, false), 0);
check("up from the first item wraps to the last", menuFocusTarget("ArrowUp", 0, 6, false), 5);
check("Home is the first item", menuFocusTarget("Home", 4, 6, false), 0);
check("End is the last item", menuFocusTarget("End", 1, 6, false), 5);
check("a one-item menu stays put", menuFocusTarget("ArrowDown", 0, 1, false), 0);
check("a letter does not move focus", menuFocusTarget("a", 2, 6, false), null);
check("Enter does not move focus (the item runs)", menuFocusTarget("Enter", 2, 6, false), null);
check("Tab does not move focus inside the menu", menuFocusTarget("Tab", 2, 6, false), null);

// ---- a menu with a node search (canvas "Add node"): 3 items, then the box
check("down from the last item reaches the search box", menuFocusTarget("ArrowDown", 2, 3, true), 3);
check("up from the first item wraps to the search box", menuFocusTarget("ArrowUp", 0, 3, true), 3);
check("up from the search box reaches the last item", menuFocusTarget("ArrowUp", 3, 3, true), 2);
check("End is the last item, not the search box", menuFocusTarget("End", 0, 3, true), 2);
check("Home from the search box is the first item", menuFocusTarget("Home", 3, 3, true), 0);

// ---- nothing to move to
check("no items, no search", menuFocusTarget("ArrowDown", 0, 0, false), null);
check("a search with no items keeps focus in the box", menuFocusTarget("ArrowUp", 0, 0, true), null);
check("a stop outside the menu", menuFocusTarget("ArrowDown", -1, 6, false), null);
check("a stop past the end", menuFocusTarget("ArrowDown", 6, 6, false), null);

if (failures) {
  console.log(`\n${failures} menu key check${failures === 1 ? "" : "s"} failed`);
  process.exit(1);
}
console.log("\nall menu key checks passed");
