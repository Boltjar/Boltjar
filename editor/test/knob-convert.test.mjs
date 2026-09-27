// ============================================================================
// Framework-free check that a knob declared promotable=false offers no Convert
// to input, on every kind of knob. Drives the REAL convertAction
// (src/lib/knobOptions.ts), transpiled with the installed TypeScript compiler
// (it has no imports), then reads InlineWidget in WorkflowNode.tsx: every
// Convert to input it hands out (a Knob's onConvert, a PromotableField's
// onPromote) must be the one convertAction returns. Run: `npm run test`.
//
// Guards the number / bool / select / plain-text path, which passed the
// promote handler straight to Knob and so offered Convert to input on a knob
// that declares it must stay a knob.
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
const { convertAction } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const ok = got === want;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${got}, want ${want})`}`);
}

const promote = () => {};
check("promotable=false offers no Convert to input", convertAction(false, promote), undefined);
check("promotable=true offers it", convertAction(true, promote), promote);
check("a widget that says nothing offers it (the declared default)", convertAction(undefined, promote), promote);

// every Convert to input InlineWidget hands out goes through convertAction.
const node = readFileSync(resolve(here, "../src/components/canvas/WorkflowNode.tsx"), "utf8");
const start = node.indexOf("function InlineWidget(");
const end = node.indexOf("\n}\n", start);
const body = node.slice(start, end);
check("InlineWidget reads promotable through convertAction",
  body.includes("const promoteHandler = convertAction(widget.promotable, onPromote);"), true);
const handed = [...body.matchAll(/\b(onConvert|onPromote)=\{([^}]*)\}/g)].map((m) => `${m[1]}=${m[2]}`);
check("InlineWidget hands out Convert to input on every knob path", handed.length >= 4, true);
for (const prop of handed) check(`${prop} is the checked handler`, prop.endsWith("=promoteHandler"), true);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall knob convert checks passed");
