// ============================================================================
// Framework-free checks of the second copy kept aside (src/lib/aside.ts): the
// buttons name both copies by what they are and their size, and every swap
// can be swapped back. Run from editor/: `node test/aside.test.mjs`.
//
// The case it guards: a browser held an older 14-node copy of a workflow whose
// saved file had 18 nodes. The console said "your unsaved edits" and offered
// "Restore my unsaved edits": read as "get my save back", it put the 14-node
// copy on the canvas and left no way back to the 18.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const read = (path) => readFileSync(resolve(here, path), "utf8");
const js = ts.transpileModule(read("../src/lib/aside.ts"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { parseAside, serializeAside, swapped, asideWords } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `\n      got  ${g}\n      want ${w}`}`);
}

const graph = (n) => ({ format: 2, name: "chat", nodes: Array.from({ length: n }, (_, i) => ({ id: `n${i}`, type: "t", config: {} })), edges: [] });

// ---- stored form
check("a bare graph from before roles reads as the older copy", parseAside(JSON.stringify(graph(14)))?.role, "older");
check("a record round-trips", parseAside(serializeAside({ role: "edits", graph: graph(3) }))?.role, "edits");
check("the saved file aside remembers what is shown", parseAside(serializeAside({ role: "saved", graph: graph(18), shown: "older" }))?.shown, "older");
check("junk reads as nothing", [parseAside("nope"), parseAside(null), parseAside('{"role":"x","graph":{}}')], [null, null, null]);

// ---- the words: both copies named, with their sizes
const older = { role: "older", graph: graph(14) };
const w1 = asideWords("chat", older, 18);
check("the saved file on the canvas, an older copy aside", w1.message, "chat: showing the saved file (18 nodes); this browser also had an older copy (14 nodes)");
check("its buttons", [w1.swap, w1.end], ["Show the older copy (14 nodes)", "Delete the older copy"]);
const w2 = asideWords("chat", swapped(older, graph(18)), 14);
check("after showing it, the way back names the saved file", w2.message, "chat: showing the older copy (14 nodes), not the saved file (18 nodes)");
check("its buttons", [w2.swap, w2.end], ["Back to the saved file (18 nodes)", "Keep the older copy (unsaved)"]);
const w3 = asideWords("chat", { role: "edits", graph: graph(19) }, 18);
check("real unsaved changes are called that", [w3.swap, w3.end], ["Show my unsaved changes (19 nodes)", "Delete my unsaved changes"]);
const w4 = asideWords("chat", swapped({ role: "edits", graph: graph(19) }, graph(18)), 19);
check("and the way back from them", [w4.swap, w4.end], ["Back to the saved file (18 nodes)", "Keep my changes (unsaved)"]);
check("no button ever says restore or discard", [w1, w2, w3, w4].flatMap((w) => [w.swap, w.end]).some((l) => /restore|discard/i.test(l)), false);

// ---- every swap swaps back
const there = swapped(older, graph(18));
check("showing the older copy puts the saved file aside", [there.role, there.shown, there.graph.nodes.length], ["saved", "older", 18]);
const back = swapped(there, graph(14));
check("going back puts the older copy aside again", [back.role, back.graph.nodes.length], ["older", 14]);

// ---- why the copy was set aside: the console says what happened. Revert to
// the saved file set the changes aside here, and the line said "was saved
// elsewhere", which nothing was.
const elsewhere = { role: "edits", graph: graph(15) };
const reverted = { role: "edits", graph: graph(15), why: "reverted" };
check("changes set aside by a save elsewhere say so", asideWords("chat", elsewhere, 16).message,
  "chat was saved elsewhere: showing the saved file (16 nodes); your unsaved changes (15 nodes) are kept");
check("changes set aside by a revert say only what is shown and kept", asideWords("chat", reverted, 16).message,
  "chat: showing the saved file (16 nodes); your unsaved changes (15 nodes) are kept");
check("a revert's buttons are the same as ever", [asideWords("chat", reverted, 16).swap, asideWords("chat", reverted, 16).end],
  ["Show my unsaved changes (15 nodes)", "Delete my unsaved changes"]);
check("why round-trips", parseAside(serializeAside(reverted))?.why, "reverted");
check("a record from before why reads as saved elsewhere",
  parseAside(JSON.stringify({ role: "edits", graph: graph(15) }))?.why, "saved-elsewhere");
check("an unknown why reads as saved elsewhere",
  parseAside(JSON.stringify({ role: "edits", graph: graph(15), why: "x" }))?.why, "saved-elsewhere");
check("the saved file aside keeps why too",
  parseAside(serializeAside({ role: "saved", graph: graph(16), shown: "edits", why: "reverted" }))?.why, "reverted");
const out = swapped(reverted, graph(16));
const home = swapped(parseAside(serializeAside(out)), graph(15));
check("why survives a swap and the swap back", [out.why, home.why, home.role], ["reverted", "reverted", "edits"]);
check("so the line after swapping back still tells the revert", asideWords("chat", home, 16).message,
  "chat: showing the saved file (16 nodes); your unsaved changes (15 nodes) are kept");

// ---- the editor uses it
const app = read("../src/App.tsx");
check("the console buttons take the words from asideWords", /asideWords\(slug, kept, canvasNodes\)/.test(app), true);
check("a swap keeps the canvas copy aside", /writeAside\(slug, swapped\(kept, canvas\)\)/.test(app), true);
check("Revert keeps unsaved changes aside", /Revert to the saved file[\s\S]{0,300}writeAside\(activeSlug, \{ role: "edits"/.test(app), true);
check("Revert marks them as reverted", /Revert to the saved file[\s\S]{0,300}writeAside\(activeSlug, \{ role: "edits", graph: toGraph\(\), why: "reverted" \}/.test(app), true);
check("a save elsewhere marks them as that", (app.match(/why: "saved-elsewhere"/g) || []).length, 2);

if (failures) {
  console.error(`\n${failures} failing check(s)`);
  process.exit(1);
}
console.log("\nall aside checks passed");
