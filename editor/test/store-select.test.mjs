// ============================================================================
// Framework-free harness for the store picker's decisions (src/lib/storeSelect.ts).
// Drives the REAL module, transpiled with the installed TypeScript compiler (it
// has no imports). Run from editor/: `node test/store-select.test.mjs`.
//
// The case it guards: a DB insert saved with table "chat_history" whose list was
// read before the table existed. The picker used to flip that value into a text
// box whose back button (drawn like the dropdown chevron) cleared it. Now the
// value stays the selection of the list, marked "not found" only when a whole
// list truly lacks it.
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
const { storeListFrom, hasTemplateRef, storeSelectMode, isMissing } = await load("storeSelect.ts");

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- what the info payload lists
const dbInfo = {
  path: "user/stores/chat.sqlite",
  tables: [
    { name: "chat_history", rows: 12, columns: [] },
    { name: "facts", rows: 0, columns: [] },
  ],
};
check("db info lists every table, in the order served", storeListFrom("tables", dbInfo),
  { names: ["chat_history", "facts"], complete: true });
check("a db with no tables is a whole, empty list", storeListFrom("tables", { path: "x", tables: [] }),
  { names: [], complete: true });
check("a db payload without tables is unreadable, not empty", storeListFrom("tables", {}),
  { names: [], complete: false });
check("a null payload is unreadable", storeListFrom("tables", null), { names: [], complete: false });
check("kv preview that holds every key is whole",
  storeListFrom("keys", { path: "x", count: 2, sample: ["mood", "name"] }),
  { names: ["mood", "name"], complete: true });
check("kv preview of 5 out of 12 keys is not whole",
  storeListFrom("keys", { path: "x", count: 12, sample: ["a", "b", "c", "d", "e"] }),
  { names: ["a", "b", "c", "d", "e"], complete: false });
check("kv preview without a count is not whole",
  storeListFrom("keys", { path: "x", sample: ["a"] }), { names: ["a"], complete: false });
check("a full kv key list is whole", storeListFrom("keys", { keys: ["a", "b"] }),
  { names: ["a", "b"], complete: true });

// ---- run-time references
check("a {tag} is a run-time reference", hasTemplateRef("{table}"), true);
check("a {tag} inside text is a run-time reference", hasTemplateRef("log_{day}"), true);
check("a {{secret.NAME}} is a run-time reference", hasTemplateRef("{{secret.TABLE_NAME}}"), true);
check("a plain table name is not", hasTemplateRef("chat_history"), false);
check("a lone brace is not", hasTemplateRef("a{b"), false);
check("an empty value is not", hasTemplateRef(""), false);

// ---- which mode
check("empty value shows the list", storeSelectMode("", null), "list");
check("a saved value the list lacks still shows the list (never a text box)", storeSelectMode("chat_history", null), "list");
check("a {tag} value opens as text", storeSelectMode("{table}", null), "typing");
check("a secret value opens as text", storeSelectMode("{{secret.TABLE}}", null), "typing");
check("\"new key\" opens a text box", storeSelectMode("", true), "typing");
check("\"new key\" keeps the text box for a typed name", storeSelectMode("session_id", true), "typing");
check("back returns to the list", storeSelectMode("session_id", false), "list");
check("back returns a {tag} value to the list", storeSelectMode("{table}", false), "list");
check("back with an empty value shows the empty list", storeSelectMode("", false), "list");

// ---- whether the saved value is missing
const whole = { names: ["chat_history", "facts"], complete: true };
const stale = { names: [], complete: true };                  // read before the table existed
const preview = { names: ["a", "b", "c", "d", "e"], complete: false };
check("nothing is missing before the list loads", isMissing("chat_history", null), false);
check("a value the whole list holds is not missing", isMissing("chat_history", whole), false);
check("a value a whole list lacks is missing", isMissing("chat_histroy", whole), true);
check("the stale list marks the saved table not found (and keeps it)", isMissing("chat_history", stale), true);
check("the list read again after the table exists clears the mark",
  isMissing("chat_history", storeListFrom("tables", dbInfo)), false);
check("a key beyond a kv preview is not called missing", isMissing("zeta", preview), false);
check("an empty value is never missing", isMissing("", whole), false);
check("a {tag} value is never missing", isMissing("{table}", whole), false);

if (failures) {
  console.log(`\n${failures} store select check${failures === 1 ? "" : "s"} failed`);
  process.exit(1);
}
console.log("\nall store select checks passed");
