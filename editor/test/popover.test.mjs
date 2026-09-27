// ============================================================================
// Framework-free harness for where the model picker's list opens
// (src/lib/popover.ts). Drives the REAL module, transpiled with the installed
// TypeScript compiler (it has no imports). Run from editor/: `node test/popover.test.mjs`.
//
// The case it guards: at 1440x900 with the canvas at 25% zoom, an LLM node near
// the bottom opened its picker far above the button. The old placement flipped
// above by the panel's largest height (340 px), while a fresh install's list
// (Auto, "Add a connection", the footer) is much shorter, so the gap between the
// button and the list was the difference. The numbers below are the picker's
// own: 264 px least width, 5 px gap, 8 px viewport margin, 160 px least room.
//
// And the case after it: at 100% in a 900 px window, a button at y 559 to 603
// opened the list above, and typing "zz" (no match, a short list) moved it
// below the button, 396 px away, because every render placed it afresh. An open
// list keeps its side, and an upward one its tallest height, so its search row
// never moves while typing. The last checks read ModelPicker.tsx, the call site.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/popover.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { placePopover, heldHeight } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const VIEW = { width: 1440, height: 900 };
const GAP = 5;
const MARGIN = 8;
const place = (trigger, height, viewport = VIEW) => placePopover({
  trigger, height, minWidth: 264, minHeight: 160, viewport, gap: GAP, margin: MARGIN,
});
/** a button at (left, top), `h` tall and `w` wide, the way getBoundingClientRect reads it. */
const button = (left, top, w = 250, h = 34) => ({ left, top, bottom: top + h, width: w });

// the reported case: at 25% the button is a quarter of its size, low on the screen
const small = button(600, 752, 250 * 0.25, 34 * 0.25);
const fresh = 170; // Auto + "Add a connection" + the footer
const p = place(small, fresh);
check("25% zoom, no room below: the list opens above", p.side, "up");
check("and its bottom edge sits the gap above the button", p.top + p.maxHeight, small.top - GAP);
check("at its own height, not the largest", p.maxHeight, fresh);
// the old placement flipped above by the largest height: top = button top - 340 - 5
const oldTop = small.top - 340 - GAP;
check("the old placement left the list 175 px above its button",
  small.top - (oldTop + fresh), 175);
check("the new one leaves the gap", small.top - (p.top + p.maxHeight), GAP);

const mid = button(400, 200);
const q = place(mid, 300);
check("room below: the list opens below", q.side, "down");
check("its top edge sits the gap under the button", q.top, mid.bottom + GAP);
check("never narrower than the least width", q.width, 264);
check("as wide as a wider button", place(button(100, 100, 400), 200).width, 400);
check("left edge on the button's left edge", q.left, 400);

check("a button near the right edge: the list stays inside the viewport",
  place(button(1400, 100, 30), 200).left, VIEW.width - MARGIN - 264);
check("a button off the left edge: the list starts at the margin",
  place(button(-120, 100, 60), 200).left, MARGIN);

// every zoom from 25% to 220%, every button height from the top to the bottom of
// the screen, every list height: the list is inside the viewport, and whenever
// one side had room for it, it touches the button across the gap.
let outside = 0;
let detached = 0;
let cases = 0;
for (const zoom of [0.25, 0.5, 0.75, 1, 1.5, 2.2]) {
  for (let y = 0; y <= VIEW.height; y += 7) {
    for (const height of [90, 170, 260, 340]) {
      const b = button(500, y, 250 * zoom, 34 * zoom);
      if (b.bottom > VIEW.height) continue;
      const r = place(b, height);
      cases += 1;
      if (r.top < MARGIN - 1e-9 || r.top + r.maxHeight > VIEW.height - MARGIN + 1e-9) outside += 1;
      const roomBelow = VIEW.height - MARGIN - (b.bottom + GAP);
      const roomAbove = b.top - GAP - MARGIN;
      if (height <= roomBelow || height <= roomAbove) {
        const touching = r.side === "down" ? r.top === b.bottom + GAP : r.top + r.maxHeight === b.top - GAP;
        if (!touching || r.maxHeight !== height) detached += 1;
      }
    }
  }
}
check("no case puts the list outside the viewport", outside, 0);
check("no case with room detaches the list from its button", detached, 0);
check("the sweep covered the zooms and heights", cases > 2900, true);

// neither side has room for the whole list: the roomier side, capped, still against the button
const short = { width: 1440, height: 420 };
const c = place(button(300, 150, 250, 34), 340, short);
check("no full fit: the roomier side (below)", c.side, "down");
check("capped to that room, so the list scrolls", c.maxHeight, 420 - MARGIN - (184 + GAP));
check("still against the button", c.top, 184 + GAP);

// a tiny viewport: too little room either side, the list takes the viewport
const tiny = { width: 800, height: 260 };
const t = place(button(300, 110, 250, 34), 340, tiny);
check("too little room on both sides: the list fills the viewport's height", t.maxHeight, 260 - 2 * MARGIN);
check("and stays inside it", t.top, MARGIN);

// a button panned out of view keeps its list on screen
const gone = place(button(500, 1200), 200);
check("a button below the viewport: the list sits at the bottom edge", gone.top + gone.maxHeight, VIEW.height - MARGIN);
const above = place(button(500, -300), 200);
check("a button above the viewport: the list sits at the top edge", above.top, MARGIN);

// ── an open list keeps its side, and an upward one its height ──
const typed = { width: 1600, height: 900 };
const at100 = { left: 700, top: 559, bottom: 603, width: 250 };
const keep = (height, side, held = 0) => placePopover({
  trigger: at100, height: heldHeight(side, height, held), minWidth: 264, minHeight: 160,
  viewport: typed, gap: GAP, margin: MARGIN, side,
});
const opened = keep(340, undefined);
check("100%, button at 559: the full list opens above", opened.side, "up");
check("its search row (the top) is at 214", opened.top, 214);
const zz = keep(120, opened.side, opened.maxHeight);
check("typing \"zz\" (a 120 px list) keeps it above", zz.side, "up");
check("with its search row where it was", zz.top, opened.top);
check("at the height it opened at", zz.maxHeight, 340);
check("clearing the search leaves it there", keep(340, zz.side, zz.maxHeight).top, opened.top);
const fresh100 = keep(120, undefined);
check("placed afresh, the same short list would sit below (the old jump)", fresh100.side, "down");
check("a downward list keeps its natural height", heldHeight("down", 120, 340), 120);
check("an upward one its tallest", heldHeight("up", 120, 340), 340);
check("and grows when the list does (a refresh)", heldHeight("up", 400, 340), 400);
const down = placePopover({ trigger: button(400, 200), height: 90, minWidth: 264, minHeight: 160,
  viewport: VIEW, gap: GAP, margin: MARGIN, side: "down" });
check("a kept downward side hangs from the button", [down.side, down.top, down.maxHeight], ["down", 239, 90]);
// panned until the kept side has too little room: then it may change sides
const pannedLow = placePopover({ trigger: button(400, 820), height: 300, minWidth: 264, minHeight: 160,
  viewport: VIEW, gap: GAP, margin: MARGIN, side: "down" });
check("a pan that leaves the kept side too little room moves it", pannedLow.side, "up");

// ── the call site ──
const picker = readFileSync(resolve(here, "../src/components/canvas/ModelPicker.tsx"), "utf8");
check("ModelPicker measures the button itself, not its wrapper",
  /const el = triggerRef\.current;/.test(picker) && /<button\s+ref=\{triggerRef\}/.test(picker), true);
check("it passes the measured height through heldHeight",
  /height: panelH === null \? PANEL_MAX_H : heldHeight\(side, panelH, tallest\)/.test(picker), true);
check("it passes the side it opened on",
  /const side = openSide \?\? undefined;/.test(picker) && /margin: VIEWPORT_MARGIN,\s*side,\s*\}\);/.test(picker), true);
check("an upward panel takes its whole height",
  /height: side === "up" \? place\.maxHeight : undefined/.test(picker), true);

if (failures) {
  console.error(`\n${failures} popover check(s) failed`);
  process.exit(1);
}
console.log("\nall popover checks passed");
