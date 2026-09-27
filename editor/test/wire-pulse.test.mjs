// ============================================================================
// Framework-free checks of which wires light while a graph runs
// (src/lib/wirePulse.ts). The server sends a `carry` event naming the drawn
// wires a value travelled: a push names the trigger wires it fired, a read
// names the one wire into the node that read it. Only those wires light, so a
// Database feeding three DB nodes lights only the wire into the one that ran.
// Drives the REAL module (and the REAL liveClassify it imports), transpiled
// with the installed TypeScript compiler, then checks the socket hook and the
// edge use it. Run from editor/: `node test/wire-pulse.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const read = (path) => readFileSync(resolve(here, path), "utf8");
function toDataUrl(file) {
  const js = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
  }).outputText.replace(/from\s+["'](\.{1,2}\/[^"']+)["']/g,
    (_, rel) => `from "${toDataUrl(resolve(dirname(file), `${rel}.ts`))}"`);
  return "data:text/javascript," + encodeURIComponent(js);
}
const load = (rel) => import(toDataUrl(resolve(here, "../src/lib", rel)));
const { carriedEdgeIds, onWirePulse, pulseWires } = await load("wirePulse.ts");
const { liveEdgeKey } = await load("liveClassify.ts");

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// The graph from the report: a Database's `db` feeds four nodes; an Interval
// fires one of them every second.
const wire = (src, sp, dst, dp) => ({ id: liveEdgeKey(src, sp, dst, dp), tuple: [src, sp, dst, dp] });
const W = {
  tick: wire("interval", "trigger", "history2", "trigger"),
  pulled: wire("database", "db", "history2", "db"),
  history: wire("database", "db", "history", "db"),
  appendUser: wire("database", "db", "append_user", "db"),
  appendAssistant: wire("database", "db", "append_assistant", "db"),
};

const hits = Object.fromEntries(Object.keys(W).map((k) => [k, 0]));
const offs = Object.entries(W).map(([k, w]) => onWirePulse(w.id, () => { hits[k] += 1; }));
const reset = () => { for (const k of Object.keys(hits)) hits[k] = 0; };

// ---- which wires a carry names
check("a carry's wires become the drawn edge ids",
  carriedEdgeIds([W.pulled.tuple]), ["database:db->history2:db"]);
check("a wire named twice lights once", carriedEdgeIds([W.tick.tuple, W.tick.tuple]), [W.tick.id]);
check("a malformed wire is skipped", carriedEdgeIds([["a", "b"], W.tick.tuple]), [W.tick.id]);

// ---- a push lights the wire it travelled
reset();
check("a push lights one listening wire", pulseWires([W.tick.tuple]), 1);
check("  the Interval's wire", hits.tick, 1);
check("  and no db wire", hits.pulled + hits.history + hits.appendUser + hits.appendAssistant, 0);

// ---- a pull lights only the wire into the node that pulled
reset();
pulseWires([W.pulled.tuple]);
check("the read lights the wire into the node that pulled", hits.pulled, 1);
check("the Database's wires into nodes that did not run stay dark",
  [hits.history, hits.appendUser, hits.appendAssistant], [0, 0, 0]);

// ---- a heartbeat: each second lights the same two wires again, nothing else
reset();
for (let s = 0; s < 3; s += 1) { pulseWires([W.tick.tuple]); pulseWires([W.pulled.tuple]); }
check("three ticks: the used wires carried three times each", [hits.tick, hits.pulled], [3, 3]);
check("three ticks: the idle wires never", [hits.history, hits.appendUser, hits.appendAssistant], [0, 0, 0]);

// ---- a chain through a Router lights every drawn wire on the way
reset();
const viaRouter = [["go", "trigger", "router", "in"], ["router", "out", "history2", "trigger"]];
const routerHits = [0, 0];
const routerOffs = viaRouter.map((t, i) => onWirePulse(liveEdgeKey(...t), () => { routerHits[i] += 1; }));
check("a carry through a Router lights both drawn wires", pulseWires(viaRouter), 2);
check("  each once", routerHits, [1, 1]);
routerOffs.forEach((off) => off());

// ---- unsubscribing: an edge removed from the canvas never hears a carry
offs.forEach((off) => off());
reset();
check("no listener left: nothing lights", pulseWires([W.pulled.tuple, W.tick.tuple]), 0);
check("  and no counter moved", Object.values(hits).reduce((a, b) => a + b, 0), 0);

// ---- the socket and the edge use it (not the per-port live values)
const socket = read("../src/hooks/useRunSocket.ts");
check("the socket hands a carry to pulseWires",
  /case "carry":\s*\n(?:\s*\/\/[^\n]*\n)*\s*pulseWires\(evt\.wires\)/.test(socket), true);
const edge = read("../src/components/canvas/TypedEdge.tsx");
check("the edge subscribes by its own id", /onWirePulse\(id,/.test(edge), true);
check("the edge no longer lights from its source's value", /liveValues/.test(edge), false);
const protocol = read("../src/types/protocol.ts");
check("the protocol declares the carry event", /kind: "carry";\s*\n\s*wires:/.test(protocol), true);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall wire pulse checks passed");
