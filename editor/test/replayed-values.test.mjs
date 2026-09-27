// ============================================================================
// Framework-free checks of how the editor tells a replayed value from a new
// one (src/lib/replayedValues.ts), and that the socket hook applies it before
// a value reaches the history the Chat viewer and Preview read. Drives the REAL
// module, transpiled with the installed TypeScript compiler (it has no
// imports). Run from editor/: `node test/replayed-values.test.mjs`.
//
// The case it guards: the connection dropped and came back, the server
// replayed the latest value of every wire, and each replay was appended to the
// port's history as a new value, so a Chat showed its last turn twice.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const read = (path) => readFileSync(resolve(here, path), "utf8");
const js = ts.transpileModule(read("../src/lib/replayedValues.ts"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { isReplayed } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// the value stream of one chat turn, then the replay a reconnect receives
const turn = [
  { key: "chat:text", id: "b-1" },
  { key: "chat:trigger", id: "b-2" },
  { key: "llm:response", id: "b-3" },
  { key: "llm:trigger", id: "b-4" },
];
{
  const ids = new Map();
  check("every live value of a turn is new", turn.map((e) => isReplayed(ids, e.key, e.id)), [false, false, false, false]);
  check("the replay after a reconnect is all known", turn.map((e) => isReplayed(ids, e.key, e.id)), [true, true, true, true]);
  check("a value that arrived while disconnected is new", isReplayed(ids, "chat:text", "b-9"), false);
  check("and its replay is then known", isReplayed(ids, "chat:text", "b-9"), true);
  check("the same id on another wire is its own value", isReplayed(ids, "tpl:out", "b-9"), false);
}
{
  const ids = new Map();
  check("a value with no id is always new", [isReplayed(ids, "k", undefined), isReplayed(ids, "k", undefined)], [false, false]);
}
{
  const ids = new Map();
  turn.forEach((e) => isReplayed(ids, e.key, e.id));
  ids.clear(); // a tab switch forgets the ids with the history
  check("after a tab switch the replay seeds the history", turn.map((e) => isReplayed(ids, e.key, e.id)), [false, false, false, false]);
}

// ---- the socket hook drops a replay before it touches any live state
const hook = read("../src/hooks/useRunSocket.ts");
const valueCase = hook.slice(hook.indexOf('case "value": {'), hook.indexOf('case "node_status":'));
const guard = valueCase.indexOf("isReplayed(");
check("the value case asks isReplayed", guard > -1, true);
check("before it records the value", guard > -1 && guard < valueCase.indexOf("setValueHistory"), true);
check("the slug switch forgets the ids", /setValueHistory\(\{\}\);\s*\n\s*lastValueIds\.current\.clear\(\);/.test(hook), true);

if (failures) {
  console.error(`\n${failures} failing check(s)`);
  process.exit(1);
}
console.log("\nall replayed value checks passed");
