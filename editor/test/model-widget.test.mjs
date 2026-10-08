// ============================================================================
// Framework-free test script for which nodes carry a model picker: it is
// declared on the model widget (kind "model" plus the family it lists,
// model_kind), never keyed off a node id, so any custom node can have one. Drives
// the REAL src/lib/dynamicPorts.ts, transpiled with the installed TypeScript
// compiler (its only imports are type-only). Run: `npm run test`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
async function load(rel) {
  const src = readFileSync(resolve(here, "../src/lib", rel), "utf8");
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  return import("data:text/javascript," + encodeURIComponent(js));
}
const { modelKindOf, modelSwitchConfig, modelWidgetOf } = await load("dynamicPorts.ts");

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const widget = (name, kind, extra = {}) => ({ name, kind, default: "", options: [], label: name, ...extra });
const def = (id, widgets) => ({
  id, name: id, kind: "transform", pulled: false, category: "AI", version: "0.1.0", summary: "",
  inputs: [], outputs: [], widgets, colors: {},
});
check("a custom node's model widget declares its family",
  modelKindOf(def("acme.voice", [widget("voice_model", "model", { model_kind: "tts" })])), "tts");
check("a model widget with no family lists LLMs",
  modelKindOf(def("acme.chat", [widget("model", "model")])), "llm");
check("a node without a model widget has no picker",
  modelKindOf(def("core.data.template", [widget("template", "code")])), null);
check("the picker widget is found by kind, not by name",
  modelWidgetOf(def("acme.voice", [widget("text", "text"), widget("voice_model", "model")]))?.name, "voice_model");

check("a core node reads the same way (the LLM declares llm)",
  modelKindOf(def("core.ai.llm", [widget("model", "model", { model_kind: "llm" })])), "llm");

// a switch writes the model under the widget's own name, whatever it is.
const voice = def("acme.voice", [widget("voice_model", "model", { model_kind: "tts" })]);
check("a model switch writes the picker widget's own key",
  modelSwitchConfig(voice, { voice_model: "acme/old" }, "acme/new", undefined),
  { voice_model: "acme/new", params: {}, promoted: [] });

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall model widget checks passed");
