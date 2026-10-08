// ============================================================================
// Framework-free harness for which widgets a node draws as knob rows
// (knobRowWidgets in src/lib/dynamicPorts.ts). Drives the REAL module,
// transpiled with the installed TypeScript compiler (its only imports are
// type-only). Run from editor/: `node test/knob-rows.test.mjs`.
//
// The case it guards: a node with a surface of its own (a model picker, a
// send box) drew no knob rows at all, so its other knobs could be set only in
// the graph JSON. The core nodes come from
// fixtures/core-nodes.json, the served definitions (tests/test_node_look.py
// keeps that file equal to the live registry), so a declaration change on
// @node shows up here. The custom node cases are built by hand: they test the generic
// mechanism, which no core node is required to exercise.
//
// It also reads WorkflowNode.tsx, the call site: a surface node must render
// InlineKnobs with the knobRowWidgets rows inside its .node-special surface.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/dynamicPorts.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { knobRowWidgets, TEMPLATE_TEXT } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const served = JSON.parse(readFileSync(resolve(here, "fixtures/core-nodes.json"), "utf8"));
const core = (id) => {
  const d = served.find((n) => n.id === id);
  if (!d) throw new Error(`fixtures/core-nodes.json has no ${id}`);
  return d;
};
const rows = (d, config = {}, drawn) => knobRowWidgets(d, config, drawn).map((w) => w.name);

// ── the core nodes, as /api/object_info serves them ──
check("Chat Input draws no knob row under its send box",
  rows(core("core.trigger.chat")), []);
check("Chat Input declares no knob: the send box's hint is fixed",
  core("core.trigger.chat").widgets, []);
for (const id of ["core.ai.llm", "core.ai.tts", "core.ai.stt", "core.ai.embed", "core.ai.rerank"]) {
  check(`${id}: the picker and its params are the whole body, no rows`, rows(core(id)), []);
}
check("the Template's text editor draws its text: no second row",
  rows(core("core.data.template"), {}, [TEMPLATE_TEXT]), []);
check("it is the surface that keeps the Template's text out",
  rows(core("core.data.template")), ["template"]);
check("the Database's hidden schema is never a row", rows(core("core.store.database")), []);
check("a Webhook that replies right away draws no timeout",
  rows(core("core.trigger.webhook")), ["path", "method", "secret", "reply"]);
check("a Webhook that waits for its Respond to Webhook draws the timeout",
  rows(core("core.trigger.webhook"), { reply: "from Respond to Webhook" }),
  ["path", "method", "secret", "reply", "timeout"]);
check("Respond to Webhook draws its four knobs",
  rows(core("core.output.respond_webhook")), ["status", "content_type", "headers", "last"]);
check("a Respond to Webhook knob promoted to a port is no longer a row",
  rows(core("core.output.respond_webhook"), { promoted: ["last"] }),
  ["status", "content_type", "headers"]);

// ── the generic mechanism, on custom nodes ──
const widget = (name, kind, extra = {}) => ({
  name, kind, default: "", options: [], label: name, surface: "body", promotable: true,
  op_field: null, op_values: [], model_kind: null, ...extra,
});
const def = (id, widgets) => ({
  id, name: id, kind: "transform", pulled: false, category: "AI", version: "0.1.0", summary: "",
  inputs: [], outputs: [], widgets, colors: {},
});
check("a custom model node draws its own knobs under its picker, found by kind",
  rows(def("acme.voice", [widget("voice_model", "model", { model_kind: "tts" }), widget("speed", "number")])),
  ["speed"]);
check("a modal widget is not a row",
  rows(def("acme.big", [widget("prompt", "code", { surface: "modal" }), widget("n", "number")])), ["n"]);
const kv = def("acme.kv", [
  widget("operation", "select", { options: ["get", "set"], default: "get" }),
  widget("value", "code", { op_field: "operation", op_values: ["set"] }),
]);
check("an op-shaped widget out of its op is not a row", rows(kv, { operation: "get" }), ["operation"]);
check("and is one in its op", rows(kv, { operation: "set" }), ["operation", "value"]);
check("rows keep the declared order", rows(def("acme.two", [widget("b", "text"), widget("a", "text")])), ["b", "a"]);

// ── the call site: WorkflowNode draws the rows inside the surface ──
const node = readFileSync(resolve(here, "../src/components/canvas/WorkflowNode.tsx"), "utf8");
const surface = node.slice(node.indexOf('<div className="node-special">'));
const surfaceEnd = surface.indexOf("{/* ── compact inline knobs");
check("WorkflowNode picks its rows with knobRowWidgets",
  /const knobWidgets = knobRowWidgets\(def, nd\.config/.test(node), true);
check("a surface node renders InlineKnobs with those rows inside .node-special",
  surfaceEnd > 0 && /<InlineKnobs[^>]*widgets=\{knobWidgets\}/s.test(surface.slice(0, surfaceEnd)), true);
const css = readFileSync(resolve(here, "../src/styles/editor.css"), "utf8");
check("rows under a surface use the params' gap token",
  /\.node-special > \.node-body\{[^}]*gap:var\(--knob-gap\)/.test(css)
  && /\.knobs\{[^}]*gap:var\(--knob-gap\)/.test(css), true);

if (failures) {
  console.error(`\n${failures} knob row check(s) failed`);
  process.exit(1);
}
console.log("\nall knob row checks passed");
