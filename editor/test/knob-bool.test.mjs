// ============================================================================
// Framework-free harness for what a bool knob shows. Drives the REAL knobBool
// (src/lib/knobOptions.ts), transpiled with the installed TypeScript compiler
// (it has no imports). Run: `npm run test`.
//
// Guards graphs saved while a toggle was still a text box: their config holds
// "false" or "0", and a truthy-string check drew the toggle on while the node
// was meant to be off. The runtime reads a saved value the same way
// (Widget.coerce), so the toggle and the running node agree.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/knobOptions.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { knobBool } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const ok = got === want;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${got}, want ${want})`}`);
}

for (const saved of ["false", "False", "0", "", "off", "no"]) check(`"${saved}" shows off`, knobBool(saved), false);
for (const saved of ["true", "True", " 1 ", "on", "yes"]) check(`"${saved}" shows on`, knobBool(saved), true);
check("true shows on", knobBool(true), true);
check("false shows off", knobBool(false), false);
check("1 shows on", knobBool(1), true);
check("0 shows off", knobBool(0), false);
check("unset shows the declared default (on)", knobBool(undefined, true), true);
check("null shows the declared default (off)", knobBool(null, false), false);
check("a saved value wins over the default", knobBool("false", true), false);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall knob-bool checks passed");
