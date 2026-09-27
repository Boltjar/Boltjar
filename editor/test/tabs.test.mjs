// ============================================================================
// Framework-free harness for the workflow tab strip (src/lib/tabs.ts): where an
// opened tab's graph comes from, and the unsaved mark a new workflow carries
// until its first Save. Drives the REAL module, transpiled with the installed
// TypeScript compiler (it has no imports). Run from editor/: `node test/tabs.test.mjs`.
//
// The case it guards: New workflow seeded an empty draft, the loader skipped a
// draft with no nodes, and so it asked GET /api/graphs/untitled, which the
// console logged as a 404. A tab never saved is never fetched now.
//
// And the two-browsers case: each browser keeps its own drafts, and a saved
// tab opened its draft whenever that had nodes, so one browser showed a 14-node
// draft of "chat" (and said "saved") while the server held the 18-node chat
// saved from the other. A draft now carries the version of the saved copy it
// started from and whether it holds edits; openPlan shows it only when it holds
// edits on the copy the server still has, and otherwise opens the saved copy
// and offers the draft back. Every row of that table is checked below.
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
  draftRecord, freeSlug, graphSource, openPlan, parseDraft, parseTabs, sameGraph,
  withTabClosed, withTabOpened, withTabRenamed, withTabSaved,
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
check("with nodes in its draft a saved graph is still fetched (the draft is judged against it)",
  graphSource(withTabOpened(BOOT, "demo"), "demo", DRAFT), "server");
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

// ---- drafts: what is stored, and old drafts read back
const node = (id, x = 0, extra = {}) => ({ id, type: "core.value.text", config: { text: id }, pos: [x, 0], ...extra });
const SAVED = { format: 2, name: "chat", nodes: [node("a"), node("b", 300)], edges: [{ src: "a", src_port: "out", dst: "b", dst_port: "in" }] };
const EDITED = { ...SAVED, nodes: [...SAVED.nodes, node("c", 600)] };
check("a draft round-trips with its base and dirty mark",
  parseDraft(draftRecord(EDITED, "v1", true)), { graph: EDITED, base: "v1", dirty: true });
check("a never-saved workflow's draft has no base",
  parseDraft(draftRecord(DRAFT, null, true)), { graph: DRAFT, base: null, dirty: true });
check("an old draft (a bare graph) reads with base and dirty unknown",
  parseDraft(JSON.stringify(EDITED)), { graph: EDITED, base: undefined, dirty: undefined });
check("nothing stored reads as no draft", parseDraft(null), null);
check("a broken draft reads as no draft", parseDraft("{oops"), null);
check("something that is not a graph reads as no draft", parseDraft(JSON.stringify({ hello: 1 })), null);

// ---- openPlan: every row of the decision
const V1 = { version: "v1" };
const V2 = { version: "v2" };
const same = (g) => sameGraph(g, SAVED);
const rec = (graph, base, dirty) => parseDraft(draftRecord(graph, base, dirty));
check("no draft: the saved copy", openPlan(null, V1, same), "server");
check("a draft with no edits: the saved copy (the draft is dropped)", openPlan(rec(SAVED, "v1", false), V1, same), "server");
check("a draft with no edits of an older copy: the saved copy, nothing offered",
  openPlan(rec(SAVED, "v0", false), V1, same), "server");
check("edits on the copy the server still has: the draft, marked unsaved", openPlan(rec(EDITED, "v1", true), V1, same), "draft");
check("edits on an older copy (saved since elsewhere): the saved copy, the draft offered back",
  openPlan(rec(EDITED, "v1", true), V2, same), "server-offer");
check("edits that deleted every node still count", openPlan(rec({ ...SAVED, nodes: [], edges: [] }, "v1", true), V1, same), "draft");
check("edits with no base on a graph the server now holds: offered back, never shown",
  openPlan(rec(EDITED, null, true), V1, same), "server-offer");
check("a server that sends no version never matches a base", openPlan(rec(EDITED, "v1", true), { version: null }, same), "server-offer");
check("an old draft that differs from the saved copy: the saved copy, the draft offered back",
  openPlan(parseDraft(JSON.stringify(EDITED)), V1, same), "server-offer");
check("an old draft that holds the saved copy: the saved copy, nothing offered",
  openPlan(parseDraft(JSON.stringify(SAVED)), V1, same), "server");
check("an old empty draft never hides the saved copy",
  openPlan(parseDraft(JSON.stringify(EMPTY_DRAFT)), V1, same), "server");
check("no saved copy: the draft when it has nodes", openPlan(rec(DRAFT, null, true), null, same), "draft");
check("no saved copy: an old draft with nodes too", openPlan(parseDraft(JSON.stringify(DRAFT)), null, same), "draft");
check("no saved copy and an empty draft: empty", openPlan(rec(EMPTY_DRAFT, null, false), null, same), "empty");
check("no saved copy and no draft: empty", openPlan(null, null, same), "empty");

// ---- the two-browsers case, end to end: the 18-node chat saved in one browser,
// a 14-node draft of it left in the other from before that save
{
  const nodes = (n) => Array.from({ length: n }, (_, i) => node(`n${i}`, i * 10));
  const saved18 = { format: 2, name: "chat", nodes: nodes(18), edges: [] };
  const stale14 = { format: 2, name: "chat", nodes: nodes(14), edges: [] };
  check("an old 14-node draft beside the saved 18-node chat opens the saved chat and offers the draft",
    openPlan(parseDraft(JSON.stringify(stale14)), V2, (g) => sameGraph(g, saved18)), "server-offer");
  check("so does a new-format 14-node draft with edits made before the save",
    openPlan(rec(stale14, "v1", true), V2, (g) => sameGraph(g, saved18)), "server-offer");
  check("and a 14-node draft with no edits is simply replaced by the saved chat",
    openPlan(rec(stale14, "v1", false), V2, (g) => sameGraph(g, saved18)), "server");
}

// ---- sameGraph: order, names and key order never count; content does
check("the same graph is the same", sameGraph(SAVED, JSON.parse(JSON.stringify(SAVED))), true);
check("node order, edge order and the name do not count",
  sameGraph(SAVED, { ...SAVED, name: "other", nodes: [...SAVED.nodes].reverse() }), true);
check("config key order does not count",
  sameGraph({ ...SAVED, nodes: [node("a", 0, { config: { x: 1, y: 2 } })] }, { ...SAVED, nodes: [node("a", 0, { config: { y: 2, x: 1 } })] }), true);
check("a moved node counts", sameGraph(SAVED, { ...SAVED, nodes: [node("a", 5), node("b", 300)] }), false);
check("sub-pixel moves do not", sameGraph(SAVED, { ...SAVED, nodes: [node("a", 0.2), node("b", 300)] }), true);
check("a changed knob counts", sameGraph(SAVED, { ...SAVED, nodes: [node("a", 0, { config: { text: "z" } }), node("b", 300)] }), false);
check("an added node counts", sameGraph(SAVED, EDITED), false);
check("a removed wire counts", sameGraph(SAVED, { ...SAVED, edges: [] }), false);
check("a disabled node counts", sameGraph(SAVED, { ...SAVED, nodes: [node("a", 0, { disabled: true }), node("b", 300)] }), false);
check("without a size rule, a size only one side has does not count",
  sameGraph(SAVED, { ...SAVED, nodes: [node("a", 0, { size: [260, 140] }), node("b", 300)] }), true);
check("a size both sides have counts",
  sameGraph({ ...SAVED, nodes: [node("a", 0, { size: [300, 140] }), node("b", 300)] },
    { ...SAVED, nodes: [node("a", 0, { size: [260, 140] }), node("b", 300)] }), false);
{
  const sizeOf = (n) => n.size ?? [260, 140];
  check("with a size rule, a default size equals no size",
    sameGraph(SAVED, { ...SAVED, nodes: [node("a", 0, { size: [260, 140] }), node("b", 300)] }, sizeOf), true);
  check("and a resize counts", sameGraph(SAVED, { ...SAVED, nodes: [node("a", 0, { size: [400, 140] }), node("b", 300)] }, sizeOf), false);
}
check("with sizes ruled out, a node the canvas grew to fit its text still matches (the editor judges old drafts so)",
  sameGraph({ ...SAVED, nodes: [node("a", 0, { size: [300, 160] }), node("b", 300)] },
    { ...SAVED, nodes: [node("a", 0, { size: [300, 208] }), node("b", 300)] }, () => null), true);
check("group member order does not count",
  sameGraph({ ...SAVED, groups: [{ id: "g", title: "G", color: "blue", members: ["a", "b"] }] },
    { ...SAVED, groups: [{ id: "g", title: "G", color: "blue", members: ["b", "a"] }] }), true);

if (failures) {
  console.error(`\n${failures} tab check(s) failed`);
  process.exit(1);
}
console.log("\nall tab checks passed");
