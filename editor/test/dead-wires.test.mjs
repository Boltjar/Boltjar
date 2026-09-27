// ============================================================================
// Framework-free harness for the dead-wire heal the editor runs on every graph
// load (src/lib/deadWires.ts): a wire whose source or target handle is not a
// port its node actually has is dropped, and each removal becomes a console
// notice. Drives the REAL healDeadWires / deadWireNotice, and through them the
// REAL concreteInputs / concreteOutputs (src/lib/dynamicPorts.ts), transpiled
// with the installed TypeScript compiler. Run from editor/:
// `node test/dead-wires.test.mjs`.
//
// Guards a real-world case: a ghost `preview.out -> tts.Username` wire (the
// TTS has no `Username` input) must heal by itself on load, while every wire
// the canvas can draw survives, and no wire is judged before its node's
// definition and (for a model-driven node) its model manifest are loaded.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
// Transpile a src module into a data: URL. deadWires.ts imports dynamicPorts.ts at
// runtime, and a data: module cannot resolve "./x", so each relative import is
// transpiled the same way and inlined as its own data: URL (type-only imports are
// erased by the transpile, so they never reach this).
function toDataUrl(file) {
  const js = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
  }).outputText.replace(/from\s+["'](\.{1,2}\/[^"']+)["']/g,
    (_, rel) => `from "${toDataUrl(resolve(dirname(file), `${rel}.ts`))}"`);
  return "data:text/javascript," + encodeURIComponent(js);
}
const load = (rel) => import(toDataUrl(resolve(here, "../src/lib", rel)));
const { healDeadWires, deadWireNotice } = await load("deadWires.ts");
const { concreteInputs, concreteOutputs } = await load("dynamicPorts.ts");

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- fixtures: node defs + manifests as /api/object_info and /api/models serve them
const port = (name, type, extra = {}) => ({
  name, type, growable: false, optional: false, trigger: false, ...extra,
});
const opOut = (name, type, ops) => port(name, type, { op_field: "operation", op_values: ops });
const widget = (name, kind, dflt) => ({ name, kind, default: dflt, options: [], label: name });
const def = (id, inputs, outputs, widgets = [], extra = {}) => ({
  id, name: id, kind: "transform", pulled: false, category: "", version: "0.1.0",
  summary: "", colors: {}, inputs, outputs, widgets, ...extra,
});
const trigger = port("trigger", "event", { trigger: true });
const DEFS = [
  def("core.ai.tts", [trigger, port("text", "text"), port("lang", "lang", { optional: true })],
    [port("audio", "audio"), port("trigger", "event")], [widget("model", "model", "xai/tts")]),
  def("core.ai.llm", [trigger, port("prompt", "text"), port("tools", "tool", { growable: true, optional: true })],
    [port("response", "text"), port("reasoning", "text"), port("trigger", "event"), port("error", "event")],
    [widget("model", "model", "")]),
  def("core.ai.tool", [], [port("call", "tool-call")], [widget("name", "text", "")]),
  def("core.output.preview", [port("in", "any", { trigger: true }), port("trigger", "event", { trigger: true })],
    [port("out", "any"), port("trigger", "event")], [], { bypass: { in: "out", trigger: "trigger" } }),
  def("core.data.template", [port("trigger", "event", { trigger: true }), port("tag", "any", { growable: true })],
    [port("out", "text"), port("trigger", "event")], [widget("template", "code", "{in}")]),
  def("core.db", [trigger, port("db", "db"), port("tag", "any", { growable: true, optional: true, ghost_base: "tag" })],
    [opOut("rows", "json", ["query", "find"]), opOut("affected", "int", ["exec", "update", "delete"]),
     opOut("row_id", "int", ["insert"]), port("trigger", "event")],
    [widget("operation", "select", "query"), widget("fields", "code", "")]),
  def("core.store.database", [], [port("db", "db")]),
  def("core.value.text", [], [port("out", "text")], [widget("text", "code", "")]),
  def("core.value.image", [], [port("out", "image")], [widget("src", "text", "")]),
  def("core.trigger.chat", [], [port("trigger", "event"), port("text", "text")]),
  def("core.flow.wireless_in", [port("in", "any", { growable: true, optional: true, ghost_base: "wire" })], [],
    [widget("channel", "select", "1")]),
  def("core.flow.wireless_out", [], [], [widget("channel", "select", "1")]),
  def("core.store.vectors", [], [port("vectors", "vectors")]),
  def("core.ai.embed", [trigger, port("text", "text")], [port("embedding", "embedding"), port("trigger", "event")]),
  def("core.vectors", [trigger, port("vectors", "vectors"),
    port("embedding", "embedding", { op_field: "operation", op_values: ["search", "index"] })],
    [opOut("affected", "int", ["delete", "clear"]), port("trigger", "event")],
    [widget("operation", "select", "search")]),
];
const defs = new Map(DEFS.map((d) => [d.id, d]));
const manifest = (id, kind, extra) => ({
  id, kind, provider: id.split("/")[0], model: id.split("/")[1], label: id, summary: "",
  context: 0, inputs: ["text"], outputs: ["text"], tools: false, thinking: false,
  thinking_style: "", json: false, params: [], ...extra,
});
const loaded = new Map([
  manifest("xai/tts", "tts", { outputs: ["audio"] }),
  manifest("xai/grok-4.20", "llm", { inputs: ["text", "image"], tools: true }),
  manifest("acme/plain", "llm", {}),
].map((m) => [m.id, m]));
const notLoaded = new Map(); // /api/models has not answered yet

// ---- graph helpers
const node = (id, type, config = {}) => ({ id, type, config, pos: [0, 0] });
const edge = (src, src_port, dst, dst_port) => ({ src, src_port, dst, dst_port });
const wireOf = (e) => `${e.src}.${e.src_port} -> ${e.dst}.${e.dst_port}`;
const heal = (nodes, edges, models = loaded) =>
  healDeadWires({ name: "t", nodes, edges }, defs, models);
const kept = (result) => result.graph.edges.map(wireOf);
const notices = (result) => result.removed.map(deadWireNotice);

// ---- 1. a real-world ghost: the TTS has no `Username` input, so it heals away
const chatNodes = [node("preview", "core.output.preview"), node("tts", "core.ai.tts", { model: "xai/tts" })];
const chatEdges = [
  edge("preview", "out", "tts", "Username"),
  edge("preview", "out", "tts", "text"),
  edge("preview", "trigger", "tts", "trigger"),
];
const healed = heal(chatNodes, chatEdges);
check("the ghost preview.out -> tts.Username is removed, the real wires stay",
  kept(healed), ["preview.out -> tts.text", "preview.trigger -> tts.trigger"]);
check("the removal becomes a console notice",
  notices(healed), ["removed a wire to a port that no longer exists: preview.out -> tts.Username"]);
check("the graph it was given is not mutated", chatEdges.length, 3);

// ---- 2. nothing is judged before its model manifest is loaded
const booting = heal(chatNodes, chatEdges, notLoaded);
check("TTS manifest not loaded yet: even the ghost is left untouched", kept(booting), chatEdges.map(wireOf));
check("TTS manifest not loaded yet: nothing is reported", notices(booting), []);

const image = [edge("cam", "out", "llm", "image")];
const llmOn = (model) => [node("cam", "core.value.image"), node("llm", "core.ai.llm", { model })];
check("an LLM image wire survives while manifests are not loaded yet",
  kept(heal(llmOn("xai/grok-4.20"), image, notLoaded)), ["cam.out -> llm.image"]);
check("an LLM image wire survives once loaded, on a model that reads images",
  kept(heal(llmOn("xai/grok-4.20"), image)), ["cam.out -> llm.image"]);
check("an LLM whose model has no manifest is never judged",
  kept(heal(llmOn("acme/retired"), image)), ["cam.out -> llm.image"]);
check("an LLM with no model picked is never judged",
  kept(heal(llmOn(""), image)), ["cam.out -> llm.image"]);
check("once loaded, an image wire into a text-only model with no tools is dead",
  notices(heal(llmOn("acme/plain"), image)), ["removed a wire to a port that no longer exists: cam.out -> llm.image"]);

// ---- 3. growable and dynamic sockets are not dead
const tool = [node("search", "core.ai.tool"), node("llm", "core.ai.llm", { model: "xai/grok-4.20" })];
check("a Tool wire on the LLM's growable tools survives",
  kept(heal(tool, [edge("search", "call", "llm", "search")])), ["search.call -> llm.search"]);

const tpl = [node("Username", "core.value.text"),
  node("tpl", "core.data.template", { template: "{username} says hi" }),
  node("old", "core.data.template", { template: "no tags left" })];
check("a Template tag wire survives",
  kept(heal(tpl, [edge("Username", "out", "tpl", "username")])), ["Username.out -> tpl.username"]);
check("a wire whose tag left the template survives (the canvas shows it as stale)",
  kept(heal(tpl, [edge("Username", "out", "old", "username")])), ["Username.out -> old.username"]);

const db = [node("chat", "core.trigger.chat"), node("database", "core.store.database"),
  node("append", "core.db", { operation: "insert", fields: "message = {tag0}" }),
  node("preview", "core.output.preview")];
const dbEdges = [edge("chat", "trigger", "append", "trigger"), edge("database", "db", "append", "db"),
  edge("chat", "text", "append", "tag0")];
check("a DB tag wire survives, with the DB's declared wires",
  kept(heal(db, dbEdges)), dbEdges.map(wireOf));
check("(precondition) the insert operation hides the DB's rows output",
  concreteOutputs(defs.get("core.db"), { operation: "insert" }).map((p) => p.name).includes("rows"), false);
check("a wire on a declared output a knob hides survives (DB rows under insert)",
  kept(heal(db, [edge("append", "rows", "preview", "in")])), ["append.rows -> preview.in"]);

const socketsOf = (operation) => concreteInputs(defs.get("core.vectors"),
  operation === undefined ? {} : { operation }, new Set()).map((p) => p.name);
check("Vectors has an embedding socket to search and index",
  [socketsOf("search"), socketsOf("index")], [["trigger", "vectors", "embedding"], ["trigger", "vectors", "embedding"]]);
check("Vectors has no embedding socket to delete or clear",
  [socketsOf("delete"), socketsOf("clear")], [["trigger", "vectors"], ["trigger", "vectors"]]);
check("a Vectors saved untouched runs its default search, so it has an embedding socket",
  socketsOf(undefined), ["trigger", "vectors", "embedding"]);
const vectors = [node("store", "core.store.vectors"), node("embed", "core.ai.embed"),
  node("forget", "core.vectors", { operation: "clear" })];
check("a wire on an input its operation hides survives (Vectors embedding under clear)",
  kept(heal(vectors, [edge("embed", "embedding", "forget", "embedding")])), ["embed.embedding -> forget.embedding"]);

const wireless = [node("Username", "core.value.text"), node("win", "core.flow.wireless_in", { channel: "1" }),
  node("wout", "core.flow.wireless_out", { channel: "1" }), node("preview", "core.output.preview")];
const wirelessEdges = [edge("Username", "out", "win", "username.out"), edge("wout", "username.out", "preview", "in")];
check("Wireless wires survive (the In socket and the Out port that mirrors it)",
  kept(heal(wireless, wirelessEdges)), wirelessEdges.map(wireOf));

// ---- 4. a node of unknown type is never judged
const unknown = [node("preview", "core.output.preview"), node("mystery", "acme.not_loaded")];
const unknownEdges = [edge("preview", "out", "mystery", "whatever"), edge("mystery", "nope", "preview", "trigger")];
check("a wire into or out of a node of unknown type survives",
  kept(heal(unknown, unknownEdges)), unknownEdges.map(wireOf));

// ---- 5. ports and nodes that are really gone
const orphans = [...wireless, node("wout2", "core.flow.wireless_out", { channel: "2" }),
  node("clock", "core.value.text"), node("preview2", "core.output.preview"), node("preview3", "core.output.preview")];
const cascade = heal(orphans, [
  ...wirelessEdges,
  edge("wout2", "username.out", "preview2", "in"), // channel 2 has no Wireless In
  edge("clock", "time", "win", "clock.time"), // the Text node has no `time` output
  edge("wout", "clock.time", "preview3", "in"), // mirrors the socket just removed
]);
check("a Wireless Out port lives only while its channel's In has that socket, in one pass",
  notices(cascade), [
    "removed a wire to a port that no longer exists: wout2.username.out -> preview2.in",
    "removed a wire to a port that no longer exists: clock.time -> win.clock.time",
    "removed a wire to a port that no longer exists: wout.clock.time -> preview3.in",
  ]);
check("... and the live Wireless wires stay", kept(cascade), wirelessEdges.map(wireOf));
check("a wire to a node that is gone is removed",
  notices(heal(chatNodes, [edge("vanished", "out", "tts", "text")])),
  ["removed a wire to a node that no longer exists: vanished.out -> tts.text"]);
check("a wire from a gone node into a node not loaded yet is left untouched",
  kept(heal(chatNodes, [edge("vanished", "out", "tts", "text")], notLoaded)), ["vanished.out -> tts.text"]);

// ---- 6. a clean graph comes back as is
const clean = { name: "t", nodes: chatNodes, edges: chatEdges.slice(1) };
const same = healDeadWires(clean, defs, loaded);
check("a graph with no dead wire is returned unchanged", same.graph === clean && same.removed.length === 0, true);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall dead-wire checks passed");
