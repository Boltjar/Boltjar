// ============================================================================
// Framework-free harness for the workflow tab strip (src/lib/tabs.ts): where an
// opened tab's graph comes from, and the unsaved mark a new workflow carries
// until its first Save. Drives the REAL module, transpiled with the installed
// TypeScript compiler (it has no imports). Run from editor/: `node test/tabs.test.mjs`.
//
// The case it guards: New workflow seeded an empty draft, the loader skipped a
// draft with no nodes, and so it asked GET /api/graphs/untitled, which the
// console logged as a 404. A tab never saved is never fetched now.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/tabs.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const {
  freeSlug, graphSource, parseTabs, withTabClosed, withTabOpened, withTabRenamed, withTabSaved,
} = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const BOOT = { open: ["chat"], active: "chat", unsaved: [] };
const EMPTY_DRAFT = { format: 1, name: "untitled", nodes: [], edges: [] };
const DRAFT = { format: 1, name: "untitled", nodes: [{ id: "a", type: "core.value.text", config: {} }], edges: [] };

// ---- a new workflow: minted around every slug in use, opened unsaved
const serverHas = new Set(["chat", "demo"]);
const slug = freeSlug("untitled", new Set([...BOOT.open, ...serverHas]));
check("a new workflow is untitled when nothing uses it", slug, "untitled");
const fresh = withTabOpened(BOOT, slug, { unsaved: true });
check("it opens at the end of the strip, active", [fresh.open, fresh.active], [["chat", "untitled"], "untitled"]);
check("marked unsaved", fresh.unsaved, ["untitled"]);
check("with no draft it opens empty, never from the server", graphSource(fresh, "untitled", null), "empty");
check("with an empty draft it opens empty, never from the server", graphSource(fresh, "untitled", EMPTY_DRAFT), "empty");
check("with nodes in its draft it opens the draft", graphSource(fresh, "untitled", DRAFT), "draft");

// ---- saved once, it loads like any saved graph
const saved = withTabSaved(fresh, "untitled");
check("a Save clears the mark", saved.unsaved, []);
check("then an empty draft never hides the server's copy", graphSource(saved, "untitled", EMPTY_DRAFT), "server");
check("saving a tab with no mark changes nothing", withTabSaved(BOOT, "chat"), BOOT);

// ---- a slug the server already has is never minted, so a Save cannot overwrite it
check("a saved graph named untitled: the new one is untitled-2",
  freeSlug("untitled", new Set(["chat", "untitled"])), "untitled-2");
check("the next free number after a run of taken ones",
  freeSlug("untitled", new Set(["untitled", "untitled-2", "untitled-3"])), "untitled-4");
check("a clone mints around its copies the same way",
  freeSlug("chat-copy", new Set(["chat", "chat-copy"])), "chat-copy-2");

// ---- the other tabs
check("an opened saved graph is not marked", withTabOpened(BOOT, "demo").unsaved, []);
check("with no draft a saved graph is fetched", graphSource(withTabOpened(BOOT, "demo"), "demo", null), "server");
check("opening the active tab again changes nothing", withTabOpened(BOOT, "chat"), BOOT);
check("opening an open tab only activates it",
  withTabOpened({ open: ["chat", "demo"], active: "chat", unsaved: [] }, "demo"),
  { open: ["chat", "demo"], active: "demo", unsaved: [] });

// ---- close and rename keep the marks in step with the strip
const two = withTabOpened(withTabOpened(BOOT, "untitled", { unsaved: true }), "demo");
check("closing an unsaved tab drops its mark", withTabClosed(two, "untitled").unsaved, []);
check("closing the active tab activates the one before it", withTabClosed(two, "demo").active, "untitled");
check("closing the first tab activates the next", withTabClosed({ ...two, active: "chat" }, "chat").active, "untitled");
check("closing the last tab leaves none active",
  withTabClosed(BOOT, "chat"), { open: [], active: null, unsaved: [] });
check("closing a tab that is not open changes nothing", withTabClosed(BOOT, "demo"), BOOT);
const renamed = withTabRenamed(two, "untitled", "notes");
check("a rename keeps the tab's place and the active tab",
  [renamed.open, renamed.active], [["chat", "notes", "demo"], "demo"]);
check("and moves the unsaved mark with it", renamed.unsaved, ["notes"]);
check("a rename onto an open tab is refused", withTabRenamed(two, "untitled", "demo"), two);

// ---- the strip read back from localStorage
check("nothing stored reads as nothing", parseTabs(null), null);
check("a broken value reads as nothing", parseTabs("{oops"), null);
check("a strip saved before the marks existed reads with none",
  parseTabs(JSON.stringify({ open: ["chat", "untitled"], active: "untitled" })),
  { open: ["chat", "untitled"], active: "untitled", unsaved: [] });
check("marks survive a refresh",
  parseTabs(JSON.stringify(fresh)), fresh);
check("a mark for a tab no longer open is dropped",
  parseTabs(JSON.stringify({ open: ["chat"], active: "chat", unsaved: ["untitled"] })).unsaved, []);
check("an active tab that is not open falls back to the first",
  parseTabs(JSON.stringify({ open: ["chat", "demo"], active: "gone", unsaved: [] })).active, "chat");
check("empty and non-string slugs are dropped",
  parseTabs(JSON.stringify({ open: ["chat", "", 3], active: "chat", unsaved: [null] })),
  { open: ["chat"], active: "chat", unsaved: [] });

if (failures) {
  console.error(`\n${failures} tab check(s) failed`);
  process.exit(1);
}
console.log("\nall tab checks passed");
