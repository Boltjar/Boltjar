// ============================================================================
// Framework-free harness for the sockets of a generic growable input (Sync's
// and a Queue's `in`, Compute's `value`). Drives the REAL concreteInputs
// (src/lib/dynamicPorts.ts), transpiled with the installed TypeScript compiler.
// Run: `npm run test`.
//
// The canvas must draw a wired socket where the server fires and validates it
// (NodeSpec.growable_base): every undeclared wire on a node with one growable
// input belongs to it, whatever its name, except a knob promoted to an input,
// which is drawn as that knob's port. Otherwise the canvas shows a wired socket
// under `in` while the problems panel says `in` is not connected.
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
const { concreteInputs } = await load("dynamicPorts.ts");

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- fixtures: the defs exactly as /api/object_info serves them
const port = (name, type, extra = {}) => ({
  name, type, growable: false, optional: false, trigger: false, ...extra,
});
const def = (id, inputs, widgets = []) => ({
  id, name: id, kind: "transform", pulled: false, category: "Flow", version: "0.1.0",
  summary: "", colors: {}, inputs, outputs: [port("out", "any")], widgets,
});
const QUEUE = def("core.flow.queue",
  [port("in", "any", { growable: true, trigger: true, ghost_base: "in" }), port("ack", "event", { trigger: true })],
  [{ name: "timeout", kind: "number", default: 300, options: [], label: "ack timeout" }]);
const SYNC = def("core.flow.sync", [port("in", "event", { growable: true, trigger: true })]);
const COMPUTE = def("core.data.compute", [port("value", "any", { growable: true })],
  [{ name: "expression", kind: "code", default: "value", options: [], label: "Expression" }]);
// a custom node with two growable inputs: a socket belongs to the base it starts with.
const TWO = def("custom.two", [port("in", "event", { growable: true, trigger: true }), port("tag", "any", { growable: true })]);

const shape = (ports) => ports.map((p) => ({
  name: p.name, type: p.type, trigger: p.trigger, base: p.base ?? null,
  promotedParam: !!p.promotedParam, ghost: !!p.ghost,
}));

// ---- 1. a Queue's promoted, wired `timeout` is its knob's port, never an `in` socket
check("Queue: a promoted, wired timeout is drawn as the knob's port",
  shape(concreteInputs(QUEUE, { promoted: ["timeout"] }, new Set(["ack", "timeout"]))),
  [
    { name: "in·+", type: "any", trigger: true, base: "in", promotedParam: false, ghost: true },
    { name: "ack", type: "event", trigger: true, base: null, promotedParam: false, ghost: false },
    { name: "timeout", type: "float", trigger: false, base: null, promotedParam: true, ghost: false },
  ]);

// ---- 2. a Queue socket named after its source is an `in` socket that fires the Queue
check("Queue: a socket named after its source belongs to `in`",
  shape(concreteInputs(QUEUE, { promoted: ["timeout"] }, new Set(["chat", "ack", "timeout"])))
    .filter((p) => p.base === "in" && !p.ghost).map((p) => [p.name, p.trigger]),
  [["chat", true]]);

// ---- 3. unpromoted, `timeout` is just a knob: no port is drawn for it
check("Queue: an unpromoted timeout draws no port",
  shape(concreteInputs(QUEUE, {}, new Set(["ack"]))).map((p) => p.name),
  ["in·+", "ack"]);

// ---- 4. Sync: every wired socket, whatever its name, is an `in` trigger socket
check("Sync: in0 and a free name both belong to `in`",
  shape(concreteInputs(SYNC, {}, new Set(["in1", "in0", "slow"]))).map((p) => [p.name, p.trigger, p.base]),
  [["in0", true, "in"], ["in1", true, "in"], ["slow", true, "in"], ["in·+", true, "in"]]);

// ---- 5. Compute: a promoted, wired expression is not a `value` socket
check("Compute: a promoted, wired expression is its knob's port",
  shape(concreteInputs(COMPUTE, { promoted: ["expression"] }, new Set(["value0", "expression"])))
    .map((p) => [p.name, p.base, p.promotedParam]),
  [["value0", "value", false], ["value·+", "value", false], ["expression", null, true]]);

// ---- 6. two growable inputs: each socket is drawn under the base it starts with, once
check("two growable inputs: a socket belongs to the base its name starts with",
  shape(concreteInputs(TWO, {}, new Set(["in0", "tag0"]))).map((p) => [p.name, p.base]),
  [["in0", "in"], ["in·+", "in"], ["tag0", "tag"], ["tag·+", "tag"]]);

if (failures) {
  console.error(`\n${failures} growable socket check(s) failed`);
  process.exit(1);
}
console.log("\nall growable socket checks passed");
