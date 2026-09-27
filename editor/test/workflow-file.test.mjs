// ============================================================================
// Framework-free harness for the file menu under the logo (src/lib/workflowFile.ts):
// what Export writes, what Open accepts and the slug an opened file takes, and
// the names Save as refuses. Drives the REAL module with its real imports
// (graphAdapter, dynamicPorts, tabs), each transpiled with the installed
// TypeScript compiler. Run from editor/: `node test/workflow-file.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
// A data: module cannot resolve "./x" by itself, so every relative import is
// transpiled the same way and its specifier pointed at that module's data: URL.
const urls = new Map();
function moduleUrl(file) {
  if (!urls.has(file)) {
    const js = ts.transpileModule(readFileSync(file, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020, removeComments: true },
    }).outputText.replace(/^((?:import|export)\b[^"']*?from\s*)"(\.\.?\/[^"]+)"/gm, (_, lead, spec) =>
      `${lead}"${moduleUrl(resolve(dirname(file), spec.endsWith(".ts") ? spec : `${spec}.ts`))}"`);
    urls.set(file, "data:text/javascript," + encodeURIComponent(js));
  }
  return urls.get(file);
}
const {
  exportFileName, exportText, openedSlug, openFailedNotice, parseWorkflowFile, saveAsName, workflowSlug,
} = await import(moduleUrl(resolve(here, "../src/lib/workflowFile.ts")));
const { serializeGraph, GRAPH_FORMAT } = await import(moduleUrl(resolve(here, "../src/lib/graphAdapter.ts")));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- Export: the editor's graph, as the server saves it
const rfNode = (id, typeId, config = {}) => ({
  id, type: "workflow", position: { x: 40, y: 80 }, data: { instanceId: id, typeId, config },
});
// the canvas as it stands after an unsaved edit: the prompt was just retyped,
// and the knobs hold secret REFERENCES (the values live in the secret store)
const edited = [
  rfNode("prompt", "core.value.text", { text: "edited, not saved yet" }),
  rfNode("llm", "core.ai.llm", { model: "openai/gpt-test", api_key: "{{secret.OPENAI_KEY}}" }),
  rfNode("call", "core.net.http", { url: "https://example.test", headers: "Authorization: Bearer {{secret.SERVICE_TOKEN}}" }),
];
const wires = [{ id: "w", source: "prompt", sourceHandle: "out", target: "llm", targetHandle: "prompt" }];
// what the canvas state would be if these references resolved: never in a graph
const SECRET_VALUES = { OPENAI_KEY: "sk-live-0123456789", SERVICE_TOKEN: "tok-9876543210" };

const text = exportText(serializeGraph("chat", edited, wires), "chat");
const file = JSON.parse(text);
check("the export carries the current graph format", file.format, GRAPH_FORMAT);
check("format is the first key, then name, as the server writes", Object.keys(file), ["format", "name", "nodes", "edges"]);
check("the export is named after the workflow's slug", file.name, "chat");
check("it is pretty-printed with two spaces", text, JSON.stringify(file, null, 2));
check("the file ends at its closing brace, like a saved graph", text.endsWith("}"), true);
check("an unsaved edit is in the export", file.nodes.find((n) => n.id === "prompt").config.text, "edited, not saved yet");
check("wires come out in the saved shape", file.edges, [{ src: "prompt", src_port: "out", dst: "llm", dst_port: "prompt" }]);
check("a model pick comes out as it is", file.nodes.find((n) => n.id === "llm").config.model, "openai/gpt-test");
check("a secret knob holds its reference", file.nodes.find((n) => n.id === "llm").config.api_key, "{{secret.OPENAI_KEY}}");
check("a reference inside text stays a reference",
  file.nodes.find((n) => n.id === "call").config.headers, "Authorization: Bearer {{secret.SERVICE_TOKEN}}");
check("no secret value is in the file", Object.values(SECRET_VALUES).filter((v) => text.includes(v)), []);
check("every mention of a secret is a {{secret.NAME}} reference",
  text.match(/secret/gi)?.length, text.match(/\{\{secret\.[A-Z_][A-Z0-9_]*\}\}/g)?.length);
check("the export renames a graph whose name drifted", JSON.parse(exportText({ format: 2, name: "old", nodes: [], edges: [] }, "new")).name, "new");
const grouped = JSON.parse(exportText(serializeGraph("g", edited, [], new Set(["call"]),
  [{ id: "grp", label: "Tools", color: "blue", members: ["call"] }]), "g"));
check("groups and disabled nodes come out", [grouped.groups[0].members, grouped.nodes.find((n) => n.id === "call").disabled], [["call"], true]);
check("the download is <slug>.json", exportFileName("my-flow"), "my-flow.json");

// ---- Open: what counts as a workflow
const workflow = (extra = {}) => JSON.stringify({
  format: 2, name: "Friend Flow", nodes: [{ id: "a", type: "core.value.text", config: { text: "hi" }, pos: [0, 0] }], edges: [], ...extra,
});
const valid = parseWorkflowFile(workflow());
check("a valid workflow opens", valid.ok, true);
check("it keeps its nodes", valid.graph.nodes.map((n) => n.id), ["a"]);
check("it arrives in the current format", valid.graph.format, GRAPH_FORMAT);

const refused = (label, text, reason) => {
  const r = parseWorkflowFile(text);
  check(label, [r.ok, r.error], [false, reason]);
};
refused("text that is not JSON is refused", "{ nodes: [", "it is not JSON");
refused("an empty file is refused", "", "it is not JSON");
refused("a JSON list is not a workflow", "[]", "it is not a workflow (no nodes and edges)");
refused("an object with no nodes is not a workflow", JSON.stringify({ name: "x", edges: [] }), "it is not a workflow (no nodes and edges)");
refused("nodes that are not a list are refused", JSON.stringify({ nodes: {}, edges: [] }), "it is not a workflow (no nodes and edges)");
refused("a package.json is not a workflow", JSON.stringify({ name: "pkg", version: "1.0.0", dependencies: {} }), "it is not a workflow (no nodes and edges)");
refused("a node with no type is refused", JSON.stringify({ nodes: [{ id: "a" }], edges: [] }), "a node has no id or type");
refused("a wire with one end is refused", JSON.stringify({ nodes: [], edges: [{ src: "a", src_port: "out", dst: "b" }] }), "a wire does not name both of its ends");
refused("a format that is not a number is refused", workflow({ format: "2" }), 'its format "2" is not a format number');
refused("a graph from a newer Boltjar is refused", workflow({ format: GRAPH_FORMAT + 1 }),
  `it was saved by a newer Boltjar (format ${GRAPH_FORMAT + 1}); this one reads formats up to ${GRAPH_FORMAT}, update Boltjar to open it`);
refused("groups that are not a list are refused", workflow({ groups: {} }), "its groups are not a list");

// an older file is migrated the way the server migrates it: format 1's "auto"
// model is gone, so that picker holds none; a real pick and references stay
const old = parseWorkflowFile(JSON.stringify({
  format: 1, name: "old", edges: [],
  nodes: [
    { id: "llm", type: "core.ai.llm", config: { model: "auto", temperature: 0.2 } },
    { id: "tts", type: "core.audio.tts", config: { model: "xai/tts", api_key: "{{secret.XAI_KEY}}" } },
  ],
}));
check("an old format opens", old.ok, true);
check("it is migrated to the current format", old.graph.format, GRAPH_FORMAT);
check("format 1's auto model is dropped, no model is picked for it", old.graph.nodes[0].config, { temperature: 0.2 });
check("a real model pick and a secret reference come through", old.graph.nodes[1].config, { model: "xai/tts", api_key: "{{secret.XAI_KEY}}" });
const unversioned = parseWorkflowFile(JSON.stringify({ nodes: [], edges: [] }));
check("a file from before formats (format 0) opens migrated", [unversioned.ok, unversioned.graph.format], [true, GRAPH_FORMAT]);
check("the refusal line names the file", openFailedNotice("notes.json", "it is not JSON"), "did not open notes.json: it is not JSON");

// ---- Open: the slug an opened file takes
const none = new Set();
check("the slug comes from the file's name field", openedSlug(valid.graph, "download.json", none), "friend-flow");
check("with no name, from the file name", openedSlug({ nodes: [], edges: [] }, "Friend's Bot.json", none), "friends-bot");
check("a file name with a folder keeps only its own name", openedSlug({ nodes: [], edges: [] }, "C:\\Users\\ada\\flow.v2.json", none), "flow-v2");
check("a name with nothing usable falls back to the file name", openedSlug({ name: "!!!", nodes: [], edges: [] }, "ideas.json", none), "ideas");
check("nothing usable anywhere is untitled", openedSlug({ name: "", nodes: [], edges: [] }, "???.json", none), "untitled");
check("a taken slug is numbered", openedSlug(valid.graph, "x.json", new Set(["friend-flow"])), "friend-flow-2");
check("numbering skips every taken slug",
  openedSlug(valid.graph, "x.json", new Set(["friend-flow", "friend-flow-2", "friend-flow-3"])), "friend-flow-4");
check("an example's slug counts as taken", openedSlug({ name: "chat", nodes: [], edges: [] }, "chat.json", new Set(["chat"])), "chat-2");

// ---- slugs: what the server keeps in a file name (letters, digits, - and _)
check("spaces and dots become one dash", workflowSlug("My Flow.v2"), "my-flow-v2");
check("dashes do not pile up or hang off the ends", workflowSlug("  -- a -- b --  "), "a-b");
check("folder separators and punctuation go", workflowSlug("a/b\\c:d?*"), "abcd");
check("underscores and digits stay", workflowSlug("Agent_2 Draft"), "agent_2-draft");
check("letters beyond ASCII stay", workflowSlug("Café Bot"), "café-bot");
check("nothing usable is an empty slug", workflowSlug("!!!"), "");
const serverKeeps = /^[\p{L}\p{N}_-]*$/u;
check("every slug is a file name the server keeps as is",
  ["My Flow.v2", "a/b", "Ünïcødé ☃ name", "tab\tand\nnewline", "x".repeat(5)].map(workflowSlug).every((s) => serverKeeps.test(s)), true);

// ---- Save as: the name is checked before anything is saved
const taken = new Set(["chat", "chat-copy", "demo"]);
check("a new name saves under its slug", saveAsName("My Copy", taken), { ok: true, slug: "my-copy" });
check("the name is trimmed", saveAsName("  notes  ", taken), { ok: true, slug: "notes" });
check("a blank name is refused", saveAsName("   ", taken), { ok: false, error: "Type a name for the copy." });
check("a name with nothing usable is refused", saveAsName("???", taken), { ok: false, error: "Use letters or numbers in the name." });
check("a taken name is refused, never overwritten", saveAsName("chat", taken), { ok: false, error: '"chat" is already a workflow. Pick another name.' });
check("a name that slugs to a taken one is refused", saveAsName("Chat Copy", taken), { ok: false, error: '"chat-copy" is already a workflow. Pick another name.' });
check("the workflow's own name is taken too", saveAsName("demo", taken).ok, false);

if (failures) {
  console.log(`\n${failures} workflow file check${failures === 1 ? "" : "s"} failed`);
  process.exit(1);
}
console.log("\nall workflow file checks passed");
