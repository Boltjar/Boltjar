// ============================================================================
// Framework-free harness for the graph the editor saves. Drives the REAL
// serializeGraph (src/lib/graphAdapter.ts) with its real import
// (dynamicPorts.ts), each transpiled with the installed TypeScript compiler.
// Run from editor/: `node test/graph-format.test.mjs`.
//
// Every saved graph carries a top-level `format` (boltjar/graph_format.py): the
// server migrates what it loads by that number, so the editor must write it on
// every save, first in the file like the server does.
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
const { serializeGraph, migrateGraph, GRAPH_FORMAT } = await import(moduleUrl(resolve(here, "../src/lib/graphAdapter.ts")));
// the core node definitions the server serves (kept equal to the registry by
// tests/test_node_look.py), which the format 3 step reads
const defs = new Map(JSON.parse(readFileSync(resolve(here, "fixtures/core-nodes.json"), "utf8"))
  .map((d) => [d.id, d]));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const node = (id, typeId, config = {}) => ({
  id, type: "workflow", position: { x: 10.4, y: 20.6 }, data: { instanceId: id, typeId, config },
});

const empty = serializeGraph("blank", [], []);
check("an empty graph carries the format", empty.format, GRAPH_FORMAT);
check("the format is the first key", Object.keys(empty)[0], "format");

const saved = serializeGraph("chat", [node("text", "core.value.text", { text: "hi" })],
  [{ id: "e", source: "text", sourceHandle: "out", target: "log", targetHandle: "in" }]);
check("a graph with nodes carries the format", saved.format, GRAPH_FORMAT);
check("the rest of the graph is unchanged", { name: saved.name, nodes: saved.nodes, edges: saved.edges }, {
  name: "chat",
  nodes: [{ id: "text", type: "core.value.text", config: { text: "hi" }, pos: [10, 21] }],
  edges: [{ src: "text", src_port: "out", dst: "log", dst_port: "in" }],
});

// ---- a draft from before format 2 (it never passed through the server)
// loads the way the server migrates it: the "auto" model is gone, so a model
// picker that held it holds none. A picked model stays exactly as saved.
const draft = {
  format: 1, name: "chat",
  nodes: [
    { id: "llm", type: "core.ai.llm", config: { model: "auto", promoted: [] }, pos: [0, 0] },
    { id: "tts", type: "core.ai.tts", config: { model: "fish/s2", params: { voice: "v" } }, pos: [1, 1] },
    { id: "http", type: "core.net.http", config: { response_type: "auto" }, pos: [2, 2] },
  ],
  edges: [],
};
const before = JSON.stringify(draft);
const migrated = migrateGraph(draft, defs);
check("the format 1 draft changed", migrated.changed, true);
check("it is in the current format", migrated.graph.format, GRAPH_FORMAT);
check("the LLM on auto holds no model", migrated.graph.nodes[0].config, { promoted: [] });
check("a picked model stays as saved", migrated.graph.nodes[1].config, { model: "fish/s2", params: { voice: "v" } });
check("an auto that is no model stays", migrated.graph.nodes[2].config, { response_type: "auto" });
check("the draft itself is not mutated", JSON.stringify(draft), before);
const current = { format: GRAPH_FORMAT, name: "x", nodes: [{ id: "llm", type: "core.ai.llm", config: { model: "auto" }, pos: [0, 0] }], edges: [] };
check("a current graph comes back as is", migrateGraph(current, defs).graph === current && !migrateGraph(current, defs).changed, true);
check("a graph with no format migrates like format 0",
  migrateGraph({ name: "old", nodes: [{ id: "a", type: "core.ai.llm", config: { model: "auto" }, pos: [0, 0] }], edges: [] }, defs).graph.nodes[0].config, {});

// ---- format 3: a data wire never fires a node. The cases are shared with the
// server's migrate (tests/test_graph_format.py), so both wire the same triggers.
const { cases } = JSON.parse(readFileSync(resolve(here, "fixtures/migrate-format-3.json"), "utf8"));
for (const c of cases) {
  const saved = JSON.stringify(c.graph);
  const out = migrateGraph(c.graph, defs);
  check(`format 3: ${c.name}`, out.graph.edges, c.edges);
  check(`format 3, the nodes stay: ${c.name}`, out.graph.nodes, c.graph.nodes);
  check(`format 3, changed says so: ${c.name}`, out.changed,
    JSON.stringify(c.edges) !== JSON.stringify(c.graph.edges));
  check(`format 3, the input is not mutated: ${c.name}`, JSON.stringify(c.graph), saved);
}

// ---- the walk visits each wire once: a long chain, many readers, a cycle
function chain(length, cyclic = false) {
  const nodes = [{ id: "go", type: "core.trigger.manual", config: {}, pos: [0, 0] },
    { id: "log", type: "core.output.log", config: {}, pos: [0, 0] }];
  const edges = [];
  for (let k = 0; k < length; k++) {
    nodes.push({ id: `c${k}`, type: "core.data.compute", config: { expression: "value0" }, pos: [0, 0] });
    const src = k ? [`c${k - 1}`, "out"] : cyclic ? [`c${length - 1}`, "out"] : ["go", "trigger"];
    edges.push({ src: src[0], src_port: src[1], dst: `c${k}`, dst_port: "value0" });
  }
  edges.push({ src: `c${length - 1}`, src_port: "out", dst: "log", dst_port: "in" });
  return { format: 2, name: "chain", nodes, edges };
}
let started = performance.now();
const long = migrateGraph(chain(5000), defs).graph;
check("a 5,000-node chain migrates in well under a second", performance.now() - started < 1000, true);
check("its Log fires on the trigger at the head", long.edges[long.edges.length - 1],
  { src: "go", src_port: "trigger", dst: "log", dst_port: "trigger" });
const readers = chain(2000);
for (let k = 0; k < 2000; k++) {
  readers.nodes.push({ id: `log${k}`, type: "core.output.log", config: {}, pos: [0, 0] });
  readers.edges.push({ src: `c${k}`, src_port: "out", dst: `log${k}`, dst_port: "in" });
}
started = performance.now();
const many = migrateGraph(readers, defs).graph;
check("2,000 Logs on one chain share the walk", performance.now() - started < 1000, true);
check("each of them is wired", many.edges.filter((e) => e.dst_port === "trigger").length, 2001);
const cycle = chain(50, true);
check("a trigger behind a cycle stays unwired", migrateGraph(cycle, defs).graph.edges, cycle.edges);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall graph-format checks passed");
