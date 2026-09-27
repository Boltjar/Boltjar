// ============================================================================
// Framework-free harness for renaming a node. Drives the REAL renameGraph
// (src/lib/renameGraph.ts) with its real imports (dynamicPorts.ts,
// graphAdapter.ts), each transpiled with the installed TypeScript compiler.
// Run from editor/: `node test/rename.test.mjs`.
//
// Guards a reported bug: a Text node `text` renamed to `Username` turned the
// UNRELATED wire preview.out -> tts.text into preview.out -> tts.Username, a
// handle TTS does not have (invisible, undeletable, BAD-TARGET-PORT). A handle
// follows a rename only on a wire FROM the renamed node into a dynamic socket
// named after it (Template tag, HTTP/DB tag, Wireless socket), never a declared
// port, and a `{tag}` token follows only in the node that owns that socket.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
// A data: module cannot resolve "./x" by itself, so every relative import is
// transpiled the same way and its specifier pointed at that module's data: URL.
// Cached per file, so a shared dependency (dynamicPorts.ts) is one instance.
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
const { renameGraph } = await import(moduleUrl(resolve(here, "../src/lib/renameGraph.ts")));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- fixtures: the node defs exactly as /api/object_info serves them
const port = (name, type, extra = {}) => ({
  name, type, growable: false, optional: false, trigger: false,
  op_field: null, op_values: [], ghost_base: null, scaffold: null, ...extra,
});
const widget = (name, kind, extra = {}) => ({
  name, kind, default: "", options: [], label: name, min: null, max: null, step: null,
  surface: "body", promotable: true, port_type: "any", template: false, accepts_secrets: false,
  op_field: null, op_values: [], placeholder: "", options_from: null, expand: false, ...extra,
});
const tmpl = (name, kind = "code") => widget(name, kind, { template: true, accepts_secrets: true });
const def = (id, name, inputs, outputs, widgets) => ({
  id, name, kind: "transform", pulled: false, category: "", version: "0.1.0", summary: "",
  inputs, outputs, widgets, colors: {}, bypass: {},
});
const tagBase = (ghost) => port("tag", "any", { growable: true, optional: true, ghost_base: ghost });
const DEFS = new Map([
  def("core.value.text", "Text", [], [port("out", "text")], [widget("text", "code", { expand: true })]),
  def("core.output.preview", "Preview",
    [port("in", "any", { trigger: true }), port("trigger", "event", { trigger: true, optional: true })],
    [port("out", "any"), port("trigger", "event")], []),
  def("core.ai.tts", "TTS",
    [port("trigger", "event", { trigger: true }), port("text", "text"), port("lang", "lang", { optional: true })],
    [port("audio", "audio"), port("trigger", "event")], [widget("model", "model", { default: "xai/tts" })]),
  def("core.data.template", "Template",
    [port("trigger", "event", { trigger: true, optional: true }), port("tag", "any", { growable: true })],
    [port("out", "text"), port("trigger", "event")], [widget("template", "code", { default: "{in}", expand: true })]),
  def("core.net.http", "HTTP Request", [port("trigger", "event", { trigger: true }), tagBase("tag")],
    [port("status", "int"), port("body", "text"), port("json", "any"), port("trigger", "event")],
    [widget("method", "select", { default: "GET" }), tmpl("url", "text"), tmpl("headers"), tmpl("query"),
     tmpl("body"), widget("response_type", "select", { default: "auto" })]),
  def("core.db", "DB", [port("trigger", "event", { trigger: true }), port("db", "db"), tagBase("tag")],
    [port("rows", "json"), port("affected", "int"), port("row_id", "int"), port("trigger", "event")],
    [widget("operation", "select", { default: "query" }), tmpl("sql"), tmpl("table", "text"), tmpl("fields"), tmpl("where")]),
  def("core.vectors", "Vectors",
    [port("trigger", "event", { trigger: true }), port("vectors", "vectors"), port("embedding", "embedding"),
     port("text", "text", { optional: true }), tagBase("tag")],
    [port("results", "memory-set"), port("ids", "json"), port("ref_id", "int"), port("affected", "int"), port("trigger", "event")],
    [widget("operation", "select", { default: "search" }), tmpl("namespace", "text"), tmpl("metadata"), tmpl("ref", "text")]),
  def("core.flow.wireless_in", "Wireless In",
    [port("in", "any", { growable: true, optional: true, ghost_base: "wire" })], [], [widget("channel", "select", { default: "1" })]),
  def("core.flow.wireless_out", "Wireless Out", [], [], [widget("channel", "select", { default: "1" })]),
  def("core.value.image", "Image", [], [port("out", "image")], [widget("src", "text")]),
  // the LLM's image/audio inputs come from its model manifest: not declared, not a
  // widget, beside a growable `tools` base that auto-numbers (tool0), never source-named.
  def("core.ai.llm", "LLM",
    [port("trigger", "event", { trigger: true }), port("prompt", "text"), port("tools", "tool", { growable: true, optional: true })],
    [port("response", "text"), port("reasoning", "text", { optional: true }), port("trigger", "event"),
     port("error", "event", { optional: true })], [widget("model", "model")]),
].map((d) => [d.id, d]));

// ---- the working graph as useGraph holds it (React Flow nodes + edges)
const node = (id, typeId, config = {}) => ({
  id, type: "workflow", position: { x: 0, y: 0 }, data: { instanceId: id, typeId, config },
});
const text = (id) => node(id, "core.value.text", { text: "" });
const wire = (src, srcPort, dst, dstPort) => ({
  id: `${src}:${srcPort}->${dst}:${dstPort}`, source: src, sourceHandle: srcPort,
  target: dst, targetHandle: dstPort, type: "typed", reconnectable: "target", data: { type: "any" },
});
const rename = (nodes, edges, from, to, groups = []) => renameGraph({ nodes, edges, groups }, DEFS, from, to);
const wires = (g) => g.edges.map((e) => `${e.source}.${e.sourceHandle} -> ${e.target}.${e.targetHandle}`);
const cfg = (g, id) => g.nodes.find((n) => n.id === id)?.data.config;
// every edge id is `src:src_port->dst:dst_port` (the live-edge key), checked per case.
const idsMatchEnds = (g) => g.edges.every((e) => e.id === `${e.source}:${e.sourceHandle}->${e.target}:${e.targetHandle}`);

// ---- 1. the reported case: renaming `text` leaves another node's `text` port alone
{
  const r = rename([text("text"), node("preview", "core.output.preview"), node("tts", "core.ai.tts")],
    [wire("preview", "out", "tts", "text")], "text", "Username");
  check("reported case: preview.out -> tts.text keeps its handle", wires(r), ["preview.out -> tts.text"]);
  check("reported case: that edge keeps its id", r.edges.map((e) => e.id), ["preview:out->tts:text"]);
  check("the node itself takes the new id and instanceId",
    r.nodes.map((n) => `${n.id}/${n.data.instanceId}`), ["Username/Username", "preview/preview", "tts/tts"]);
}
{
  const r = rename([text("text"), node("tts", "core.ai.tts")], [wire("text", "out", "tts", "text")], "text", "Username");
  check("a wire FROM the renamed node into a declared port keeps the port", wires(r), ["Username.out -> tts.text"]);
  check("  and its id follows the new source", r.edges.map((e) => e.id), ["Username:out->tts:text"]);
}
{
  const r = rename([text("text"), node("vec", "core.vectors", { operation: "search" })],
    [wire("text", "out", "vec", "text")], "text", "Username");
  check("a declared port on a node with tag sockets (Vectors.text) keeps its name",
    wires(r), ["Username.out -> vec.text"]);
}
{
  // an Image node's default id is `image`, the same name as the LLM input it feeds.
  const r = rename([node("image", "core.value.image", { src: "" }), node("llm", "core.ai.llm", { model: "xai/grok-4.20" })],
    [wire("image", "out", "llm", "image")], "image", "Photo");
  check("an LLM modality port (image) fed by a node named `image` keeps its name",
    wires(r), ["Photo.out -> llm.image"]);
}

// ---- 2. a Template tag named after the renamed node follows it, and only its token
{
  const nodes = [text("text"), text("persona"), text("other"),
    node("tpl", "core.data.template", { template: "Hi {text}, I am {persona}" }),
    node("tpl2", "core.data.template", { template: "Echo {text}" }),
    node("http", "core.net.http", { method: "POST", url: "https://", body: "{text}" })];
  const edges = [wire("text", "out", "tpl", "text"), wire("persona", "out", "tpl", "persona"),
    wire("other", "out", "tpl2", "text")];
  const r = rename(nodes, edges, "text", "Username");
  check("the tag socket fed by the renamed node follows it (slugged)", wires(r),
    ["Username.out -> tpl.username", "persona.out -> tpl.persona", "other.out -> tpl2.text"]);
  check("its {token} follows in that Template", cfg(r, "tpl").template, "Hi {username}, I am {persona}");
  check("another Template's {text}, fed by another node, is untouched", cfg(r, "tpl2").template, "Echo {text}");
  check("a node not wired from the renamed node keeps its {text}", cfg(r, "http").body, "{text}");
  check("every edge id matches its ends", idsMatchEnds(r), true);
  check("the input graph is not mutated",
    [nodes[3].data.config.template, edges[0].targetHandle, edges[0].source], ["Hi {text}, I am {persona}", "text", "text"]);
}
{
  const r = rename([text("text"), node("tpl", "core.data.template", { template: "Hi {text}" })],
    [wire("text", "out", "tpl", "text")], "text", "Chat Append (User)");
  check("a new name with spaces and symbols slugs like a ghost drop", wires(r), ["Chat Append (User).out -> tpl.chat_append_user"]);
  check("  and the token takes the slug", cfg(r, "tpl").template, "Hi {chat_append_user}");
}
{
  const r = rename([text("text"), text("persona"), node("tpl", "core.data.template", { template: "Hi {text}, I am {persona}" })],
    [wire("text", "out", "tpl", "text"), wire("persona", "out", "tpl", "persona")], "text", "Persona");
  check("a slug another socket already owns is numbered, never merged", wires(r),
    ["Persona.out -> tpl.persona2", "persona.out -> tpl.persona"]);
  check("  and the token takes the numbered name", cfg(r, "tpl").template, "Hi {persona2}, I am {persona}");
}
{
  const r = rename([text("text"), node("tpl", "core.data.template", { template: "Hi {text}, {username}!" })],
    [wire("text", "out", "tpl", "text")], "text", "Username");
  check("a slug an unwired tag already uses is numbered too", wires(r), ["Username.out -> tpl.username2"]);
  check("  and the existing {username} stays its own tag", cfg(r, "tpl").template, "Hi {username2}, {username}!");
}
{
  const r = rename([text("text"), node("tpl", "core.data.template", { template: "Hi {text}" })],
    [wire("text", "out", "tpl", "text")], "text", "Text");
  check("a case-only rename keeps the same slug: the socket stays (not text2)", wires(r), ["Text.out -> tpl.text"]);
  check("  and the token stays", cfg(r, "tpl").template, "Hi {text}");
}
{
  const r = rename([text("text"), node("tpl", "core.data.template", { template: "{greeting} world" })],
    [wire("text", "out", "tpl", "greeting")], "text", "Username");
  check("an author-named tag the renamed node feeds keeps its name", wires(r), ["Username.out -> tpl.greeting"]);
  check("  and the Template text is untouched", cfg(r, "tpl").template, "{greeting} world");
}

// ---- 3. an HTTP / DB tag socket named after its source follows the rename
{
  const r = rename([text("text"),
    node("http", "core.net.http", { method: "POST", url: "https://api.x/{text}?q={text}", body: '{"name": "{text}", "who": "{texts}"}' }),
    node("db", "core.db", { operation: "query", sql: "SELECT * FROM users WHERE name = {text}" })],
    [wire("text", "out", "http", "text"), wire("text", "out", "db", "text")], "text", "Username");
  check("the HTTP and DB tag sockets follow", wires(r), ["Username.out -> http.username", "Username.out -> db.username"]);
  check("every {text} in the HTTP url follows", cfg(r, "http").url, "https://api.x/{username}?q={username}");
  check("only the exact token follows in the body ({texts} stays)", cfg(r, "http").body, '{"name": "{username}", "who": "{texts}"}');
  check("a non-template knob is untouched", cfg(r, "http").method, "POST");
  check("the DB sql token follows", cfg(r, "db").sql, "SELECT * FROM users WHERE name = {username}");
  check("every edge id matches its ends", idsMatchEnds(r), true);
}
{
  const r = rename([text("url"), node("http", "core.net.http", { url: "https://", promoted: ["url"] })],
    [wire("url", "out", "http", "url")], "url", "Endpoint");
  check("a promoted knob port (http.url) is not a tag: it keeps its name", wires(r), ["Endpoint.out -> http.url"]);
  check("  and stays promoted", cfg(r, "http").promoted, ["url"]);
}

// ---- 4. wires INTO the renamed node keep their handles
{
  const r = rename([text("persona"), text("text"), node("tts", "core.ai.tts"),
    node("tpl", "core.data.template", { template: "Hi {text}, I am {persona}" })],
    [wire("persona", "out", "tpl", "persona"), wire("text", "out", "tpl", "text"), wire("tpl", "out", "tts", "text")],
    "tpl", "Prompt");
  check("wires into the renamed Template keep their tag handles", wires(r),
    ["persona.out -> Prompt.persona", "text.out -> Prompt.text", "Prompt.out -> tts.text"]);
  check("  and its own template text is untouched", cfg(r, "Prompt").template, "Hi {text}, I am {persona}");
  check("  and their ids follow the new target", r.edges.map((e) => e.id),
    ["persona:out->Prompt:persona", "text:out->Prompt:text", "Prompt:out->tts:text"]);
}

// ---- 5. a Wireless socket follows, and the Out wires on its channel follow with it
{
  const r = rename([text("answer"), text("Answer"),
    node("win", "core.flow.wireless_in", { channel: "1" }), node("win2", "core.flow.wireless_in", { channel: "2" }),
    node("wout", "core.flow.wireless_out", { channel: "1" }), node("wout2", "core.flow.wireless_out", { channel: "2" }),
    node("tts", "core.ai.tts"), node("tts2", "core.ai.tts")],
    [wire("answer", "out", "win", "answer.out"), wire("Answer", "out", "win2", "answer.out"),
     wire("wout", "answer.out", "tts", "text"), wire("wout2", "answer.out", "tts2", "text")],
    "answer", "reply");
  check("the Wireless In socket and its channel's Out wire follow; channel 2 is untouched", wires(r),
    ["reply.out -> win.reply.out", "Answer.out -> win2.answer.out", "wout.reply.out -> tts.text", "wout2.answer.out -> tts2.text"]);
  check("every edge id matches its ends", idsMatchEnds(r), true);
}
{
  // two Ins on channel 1 (flagged duplicate-channel): the Out mirrors the FIRST
  // (win, fed by `Answer`), so renaming `answer`, which feeds the second, must
  // not move the Out's wire off a socket that still exists.
  const r = rename([text("answer"), text("Answer"),
    node("win", "core.flow.wireless_in", { channel: "1" }), node("dup", "core.flow.wireless_in", { channel: "1" }),
    node("wout", "core.flow.wireless_out", { channel: "1" }), node("tts", "core.ai.tts")],
    [wire("Answer", "out", "win", "answer.out"), wire("answer", "out", "dup", "answer.out"),
     wire("wout", "answer.out", "tts", "text")],
    "answer", "reply");
  check("an Out wire follows only the channel's first In", wires(r),
    ["Answer.out -> win.answer.out", "reply.out -> dup.reply.out", "wout.answer.out -> tts.text"]);
}

// ---- 6. a rename that cannot apply changes nothing
{
  const nodes = [text("text"), text("persona"), node("tts", "core.ai.tts")];
  const edges = [wire("text", "out", "tts", "text")];
  check("onto another node's id: refused (no wire moves to that node)", rename(nodes, edges, "text", "persona"), null);
  check("to the same id: nothing to do", rename(nodes, edges, "text", "text"), null);
  check("to a blank name: refused", rename(nodes, edges, "text", "   "), null);
  check("an unknown node: nothing to do", rename(nodes, edges, "nope", "x"), null);
}

// ---- 7. the node keeps its group
{
  const r = rename([text("text"), node("tts", "core.ai.tts")], [],
    "text", "Username", [{ id: "group-1", title: "Voice", color: "teal", members: ["text", "tts"] }]);
  check("a grouped node stays in its group", r.groups.map((g) => g.members), [["Username", "tts"]]);
}

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall rename checks passed");
