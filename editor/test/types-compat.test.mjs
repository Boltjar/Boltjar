// ============================================================================
// Minimal, framework-free harness for the connection type checker. No test
// runner is wired in the editor, so this drives the REAL `typesCompatible`
// (src/lib/types.ts) by transpiling that single file with the installed
// TypeScript compiler and importing the emitted module. Run: `npm run test`
// (or `node test/types-compat.test.mjs`) from the editor/ directory.
//
// Guards the Tool -> LLM regression: a Tool node's `call` output is `tool-call`
// and the LLM's growable `tools` input is `tool`; the drop is validated by
// typesCompatible(out, in), which must accept tool-call into a tool input
// (subtype) while staying directional.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/types.ts"), "utf8");
// transpileModule ignores (erases) the type-only import, so no protocol dep is
// pulled; we just need the runtime function bodies.
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const mod = await import(
  "data:text/javascript," + encodeURIComponent(js)
);
const { typesCompatible } = mod;

let failures = 0;
function check(label, got, want) {
  const ok = got === want;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}  (got ${got}, want ${want})`);
}

// The bug: dragging Tool.call (tool-call) onto the LLM's tools ghost (base type
// tool). Must now be accepted.
check("Tool.call (tool-call) -> LLM.tools (tool)", typesCompatible("tool-call", "tool"), true);
// Directional: a bare tool must not satisfy a tool-call input (Tool Args.call).
check("tool -> tool-call is directional (rejected)", typesCompatible("tool", "tool-call"), false);
// A non-tool output must still be rejected by the tools port.
check("event -> tool rejected", typesCompatible("event", "tool"), false);
// Other declared backend subtypes the editor now mirrors.
check("lang -> text", typesCompatible("lang", "text"), true);
check("utterance -> message", typesCompatible("utterance", "message"), true);
check("vectors -> db", typesCompatible("vectors", "db"), true);
// List.out (list) must drop onto a json input (Format List / DB rows); directional.
check("list -> json accepted", typesCompatible("list", "json"), true);
check("json -> list is directional (rejected)", typesCompatible("json", "list"), false);
// Baselines that must not regress.
check("any wildcard", typesCompatible("anything", "any"), true);
check("numeric family int->float", typesCompatible("int", "float"), true);
check("text -> tool rejected", typesCompatible("text", "tool"), false);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall type-compat checks passed");
