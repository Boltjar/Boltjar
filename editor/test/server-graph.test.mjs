// ============================================================================
// Framework-free harness for reading a saved graph from the server. Drives the
// REAL fetchServerGraph / serverError (src/lib/serverGraph.ts), transpiled with
// the installed TypeScript compiler, against canned responses.
// Run from editor/: `node test/server-graph.test.mjs`.
//
// Guards a reported bug: the editor read every failed GET as "no such graph",
// so a graph from a newer Boltjar (a 422) opened as an empty, editable canvas
// that Ctrl+S then saved over the file, and a rename read the 422 as a free
// slug. Only a 404 is a new graph now; anything else is reported, not opened.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const js = ts.transpileModule(readFileSync(resolve(here, "../src/lib/serverGraph.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020, removeComments: true },
}).outputText;
const { fetchServerGraph, serverError, unreadableNotice } =
  await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// a `get` that answers every request with `res` and records the URLs asked for.
function answering(res) {
  const urls = [];
  const get = async (url) => {
    urls.push(url);
    if (res instanceof Error) throw res;
    return res;
  };
  return { get, urls };
}
const json = (body, status) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

const GRAPH = { format: 1, name: "chat", nodes: [{ id: "a", type: "core.value.text", config: {} }], edges: [] };
const NEWER = "the graph was saved by a newer Boltjar (format 2); this one reads formats up to 1, update Boltjar to open it";

// ---- a graph the server serves opens as that graph
{
  const { get, urls } = answering(json(GRAPH, 200));
  check("a served graph opens as the graph", await fetchServerGraph("chat", get), { kind: "graph", graph: GRAPH });
  check("it is asked for by slug", urls, ["/api/graphs/chat"]);
}

// ---- only a 404 is a new graph (an empty canvas, a free slug for a rename)
check("a 404 is a missing graph",
  await fetchServerGraph("fresh", answering(json({ error: "not found" }, 404)).get), { kind: "missing" });

// ---- every other failure is a graph that may exist and cannot be shown
check("a 422 is unreadable, with the server's reason",
  await fetchServerGraph("future", answering(json({ error: NEWER }, 422)).get), { kind: "unreadable", error: NEWER });
check("a server error without a JSON body is unreadable, with the status",
  await fetchServerGraph("broken", answering(new Response("Internal Server Error", { status: 500 })).get),
  { kind: "unreadable", error: "the server answered 500" });
check("no answer at all is unreadable",
  await fetchServerGraph("chat", answering(new TypeError("Failed to fetch")).get),
  { kind: "unreadable", error: "the server did not answer" });
check("an OK answer that is not JSON is unreadable",
  await fetchServerGraph("chat", answering(new Response("<html>", { status: 200 })).get),
  { kind: "unreadable", error: "the server's answer is not a graph" });

// ---- slugs are encoded into the URL
{
  const { get, urls } = answering(json({ error: "not found" }, 404));
  await fetchServerGraph("my graph/1", get);
  check("the slug is URL-encoded", urls, ["/api/graphs/my%20graph%2F1"]);
}

// ---- a refused save reads the same way
check("a refused save gives the server's reason",
  await serverError(json({ error: `the saved graph is kept, this Boltjar cannot read it: ${NEWER}` }, 409)),
  `the saved graph is kept, this Boltjar cannot read it: ${NEWER}`);
check("an error body without a string `error` falls back to the status",
  await serverError(json({ error: { code: 7 } }, 400)), "the server answered 400");

check("the console line names the graph and the reason",
  unreadableNotice("future", NEWER), `did not open future: ${NEWER}`);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall server-graph checks passed");
