// ============================================================================
// Framework-free harness for the ⌘K palette's hover (src/lib/pointerGate.ts and
// its use in src/components/CommandPalette.tsx). Drives the REAL gate,
// transpiled with the installed TypeScript compiler (it has no imports), then
// reads the palette's source for the wiring. Run from editor/:
// `node test/palette-hover.test.mjs`.
//
// The reported case: with the pointer resting over the list (a double-click on
// the canvas opens the palette under it), typing "LLM" reordered the rows, the
// browser sent the row now under the pointer a mouseenter, and that row took
// the highlight from the best match: Enter added Sentences, not LLM.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/pointerGate.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { pointerGate } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// the palette's two handlers, as CommandPalette.tsx wires them (checked below)
function palette() {
  const gate = pointerGate();
  const p = {
    active: 0,
    type() { p.active = 0; }, // onChange: a new query puts Enter on its best match
    move(index, x, y) { if (gate.moved(x, y)) p.active = index; }, // a row's onMouseMove
  };
  return p;
}

{
  const p = palette();
  // the pointer rests over the list at (800, 450), where row 3 is drawn
  p.move(3, 800, 450); // the first move the gate sees only records where it is
  check("opening under a resting pointer keeps the best match", p.active, 0);
  p.type();
  p.move(6, 800, 450); // rows reordered: the row now under it gets a same-point move
  check("typing with the pointer resting over row 3 keeps row 0 active", p.active, 0);
  p.type();
  p.move(2, 800, 450);
  check("and so does pasting the whole query", p.active, 0);
}
{
  const p = palette();
  p.move(3, 800, 450);
  p.move(3, 801, 452);
  check("a real move picks the row under the pointer", p.active, 3);
  p.move(4, 801, 480);
  check("and the next row it moves onto", p.active, 4);
  p.type();
  check("a new query puts Enter back on the best match", p.active, 0);
  p.move(5, 801, 480);
  check("the reordered row under the still pointer does not take it", p.active, 0);
  p.move(5, 803, 481);
  check("until the pointer moves again", p.active, 5);
}

const gate = pointerGate();
check("the first move is not a move", gate.moved(10, 10), false);
check("the same point is not a move", gate.moved(10, 10), false);
check("a new x is a move", gate.moved(11, 10), true);
check("a new y is a move", gate.moved(11, 12), true);

// ── the wiring in CommandPalette.tsx ──
const pal = readFileSync(resolve(here, "../src/components/CommandPalette.tsx"), "utf8");
check("no row picks itself on mouseenter or mouseover", /onMouse(Enter|Over)=/.test(pal), false);
check("a row picks itself on a mousemove the gate calls a move",
  /onMouseMove=\{\(e\) => \{\s*if \(hover\.current\.moved\(e\.clientX, e\.clientY\)\) setActive\(index\);/.test(pal), true);
check("the gate lives for the palette's lifetime", /const hover = useRef\(pointerGate\(\)\)/.test(pal), true);
check("a new query resets the active row to the best match",
  /setQuery\(e\.target\.value\);[\s\S]{0,120}setActive\(0\)/.test(pal), true);

if (failures) {
  console.error(`\n${failures} palette hover check(s) failed`);
  process.exit(1);
}
console.log("\nall palette hover checks passed");
