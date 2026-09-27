// ============================================================================
// Framework-free test script for the Connections window's endpoint form rules
// (src/lib/endpoints.ts) and the note each endpoint row shows (listingNote in
// src/lib/modelMeta.ts). Drives the REAL modules, transpiled with the
// installed TypeScript compiler (their only imports are type-only). Run:
// `npm run test`.
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
const { baseUrlProblem, endpointKeySecret, endpointNameProblem } = await load("endpoints.ts");
const { listingNote } = await load("modelMeta.ts");

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- the name: the provider id its models carry
check("a plain name is fine", endpointNameProblem("openrouter", []), null);
check("digits, - and _ are fine", endpointNameProblem("my-vllm_2", []), null);
check("an empty name is required", endpointNameProblem("", []), "Name is required.");
check("spaces are refused", endpointNameProblem("my vllm", []) !== null, true);
check("uppercase is refused (the field lowercases as you type)", endpointNameProblem("Groq", []) !== null, true);
check("a leading dash is refused", endpointNameProblem("-groq", []) !== null, true);
check("33 characters is one too many", endpointNameProblem("a".repeat(33), []) !== null, true);
check("an endpoint already added is named",
  endpointNameProblem("groq", ["groq"]), "An endpoint with this name already exists.");

// ---- where a typed key goes (the server's secret_name_for)
check("openrouter's key", endpointKeySecret("openrouter"), "OPENROUTER_API_KEY");
check("dashes become underscores", endpointKeySecret("my-vllm"), "MY_VLLM_API_KEY");

// ---- the base URL
check("an https URL with its version path", baseUrlProblem("https://openrouter.ai/api/v1"), null);
check("a bare local host (the server adds /v1)", baseUrlProblem("http://localhost:1234"), null);
check("an empty URL is required", baseUrlProblem("  "), "Base URL is required.");
check("a host without a scheme is refused", baseUrlProblem("localhost:1234") !== null, true);

// ---- the note on an endpoint's row, from its last listing
const listing = (extra = {}) => ({
  ok: true, checked: "2026-09-27T11:55:00Z", updated: "2026-09-27T11:55:00Z", error: null,
  failure: null, count: 12, ...extra,
});
check("never asked", listingNote(undefined), { ok: false, text: "not checked yet" });
check("answered", listingNote(listing()), { ok: true, text: "12 models" });
check("one model", listingNote(listing({ count: 1 })), { ok: true, text: "1 model" });
check("a local server that is down",
  listingNote(listing({ ok: false, failure: "not_running" })), { ok: false, text: "not running" });
check("a refused key",
  listingNote(listing({ ok: false, failure: "key_refused" })), { ok: false, text: "refused the key" });

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall endpoint checks passed");
