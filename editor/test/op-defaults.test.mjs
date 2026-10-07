// ============================================================================
// One rule for an operation-shaped node: a missing config value means the
// widget's declared default, for its knobs, its inputs and its outputs alike.
// A node saved through the API or MCP is often `config: {}`; the operation
// knob then shows its default (Vectors "search"), and the node must show that
// operation's knobs and ports too. Before, inputs read the default but knobs
// and outputs read the raw config, so a config-less Vectors showed "search"
// with no results/ids outputs and no top k/min score knobs.
//
// Drives the REAL dynamicPorts module (transpiled with the installed
// TypeScript compiler) on the served core definitions (fixtures/core-nodes.json,
// kept equal to the live registry by tests/test_node_look.py). The fixture
// carries no outputs, so the op-shaped outputs below are copied from the
// @node declarations in boltjar/nodes/core/builtin.py.
// Run from editor/: `node test/op-defaults.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const js = ts.transpileModule(readFileSync(resolve(here, "../src/lib/dynamicPorts.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { knobRowWidgets, concreteInputs, concreteOutputs, opVisible, configValue } =
  await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const core = JSON.parse(readFileSync(resolve(here, "fixtures/core-nodes.json"), "utf8"));
const byId = new Map(core.map((d) => [d.id, d]));
const out = (name, type, ops) => ({ name, type, growable: false, optional: false, trigger: false,
  ...(ops ? { op_field: "operation", op_values: ops } : {}) });
const OUTPUTS = {
  "core.vectors": [out("results", "memory-set", ["search"]), out("ids", "json", ["search"]),
    out("ref_id", "int", ["index"]), out("affected", "int", ["delete", "clear"]), out("trigger", "event")],
  "core.db": [out("rows", "json", ["query", "find"]), out("affected", "int", ["exec", "update", "delete"]),
    out("row_id", "int", ["insert"]), out("trigger", "event")],
  "core.kv": [out("value", "any", ["get"]), out("ok", "bool", ["has"]), out("keys", "json", ["keys"]),
    out("trigger", "event")],
};
const def = (id) => ({ ...byId.get(id), outputs: OUTPUTS[id] ?? byId.get(id).outputs ?? [] });
const names = (xs) => xs.map((x) => x.name);
const knobs = (id, config) => names(knobRowWidgets(def(id), config));
const outs = (id, config) => names(concreteOutputs(def(id), config));
const ins = (id, config) => names(concreteInputs(def(id), config, new Set()));

// ---- the value rule itself
const widgets = [{ name: "operation", default: "search" }];
check("a missing value is the default", configValue(widgets, {}, "operation"), "search");
check("a null value is the default", configValue(widgets, { operation: null }, "operation"), "search");
check("a saved value wins", configValue(widgets, { operation: "clear" }, "operation"), "clear");
check("opVisible reads the default when the operation is missing",
  opVisible({ op_field: "operation", op_values: ["search"] }, {}, widgets), true);

// ---- the cases that were broken: config-less nodes
check("Vectors {} has the search outputs", outs("core.vectors", {}), ["results", "ids", "trigger"]);
check("Vectors {} has the search knobs", knobs("core.vectors", {}), ["operation", "namespace", "top_k", "min_score"]);
check("DB {} has the query outputs", outs("core.db", {}), ["rows", "trigger"]);
check("DB {} has the query knobs", knobs("core.db", {}), ["operation", "sql"]);
check("KV {} has the get outputs", outs("core.kv", {}), ["value", "trigger"]);
check("KV {} has the get knobs", knobs("core.kv", {}), ["operation", "key"]);

// ---- the rule holds for every op-shaped core node: {} reads exactly as its default saved
for (const d of core) {
  const fields = new Set([...d.widgets, ...d.inputs].map((x) => x.op_field).filter(Boolean));
  for (const field of fields) {
    const w = d.widgets.find((x) => x.name === field);
    if (!w) continue;
    const saved = { [field]: w.default };
    check(`${d.id}: {} draws the knobs of ${field} = ${JSON.stringify(w.default)}`, knobs(d.id, {}), knobs(d.id, saved));
    check(`${d.id}: {} has the inputs of ${field} = ${JSON.stringify(w.default)}`, ins(d.id, {}), ins(d.id, saved));
    check(`${d.id}: {} has the outputs of ${field} = ${JSON.stringify(w.default)}`, outs(d.id, {}), outs(d.id, saved));
  }
}

// ---- a saved operation still reshapes everything
check("Vectors under clear has only its count", outs("core.vectors", { operation: "clear" }), ["affected", "trigger"]);
check("DB under insert has the insert knobs", knobs("core.db", { operation: "insert" }), ["operation", "table", "fields"]);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nop defaults: all checks passed");
