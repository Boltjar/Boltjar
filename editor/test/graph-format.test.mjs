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
const { serializeGraph, GRAPH_FORMAT } = await import(moduleUrl(resolve(here, "../src/lib/graphAdapter.ts")));

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

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall graph-format checks passed");
