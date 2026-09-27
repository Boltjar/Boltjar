// ============================================================================
// Framework-free check of the editor's boot. Drives the REAL startSession
// (src/lib/session.ts), transpiled with the installed TypeScript compiler.
// Run from editor/: `node test/session.test.mjs`.
//
// The server rejects /api and /ws calls without its session cookie, so the
// cookie request must finish before the editor renders and starts fetching,
// and a failed request must still render the editor.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const js = ts.transpileModule(readFileSync(resolve(here, "../src/lib/session.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { startSession } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  if (g === w) {
    console.log(`ok   ${label}`);
  } else {
    failures++;
    console.log(`FAIL ${label}\n     got  ${g}\n     want ${w}`);
  }
}

// 1. the editor starts only after /api/session has answered.
{
  const log = [];
  let answer;
  const fetchImpl = (url) => {
    log.push(`fetch ${url}`);
    return new Promise((resolve) => { answer = resolve; });
  };
  const booted = startSession(() => log.push("start"), fetchImpl);
  await Promise.resolve();
  check("nothing renders while the session call is in flight", log, ["fetch /api/session"]);
  answer({ ok: true });
  await booted;
  check("the editor renders once the cookie is set", log, ["fetch /api/session", "start"]);
}

// 2. a server that cannot be reached still gets an editor.
{
  const log = [];
  await startSession(() => log.push("start"), () => Promise.reject(new TypeError("Failed to fetch")));
  check("a failed session call still renders the editor", log, ["start"]);
}

if (failures) {
  console.log(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log("\nall session checks passed");
