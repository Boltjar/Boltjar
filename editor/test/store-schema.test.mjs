// ============================================================================
// Framework-free harness for declared store schemas (src/lib/storeSchema.ts)
// and the body rule that keeps a hidden widget off the node (onBody in
// src/lib/dynamicPorts.ts). Drives the REAL modules, transpiled with the
// installed TypeScript compiler (their only imports are type-only).
// Run from editor/: `node test/store-schema.test.mjs`.
//
// The case it guards: the chat example used a "Chat Setup" node that ran
// CREATE TABLE, so Chat Append's table list stayed empty until it fired. Now
// the Database node declares its tables in the graph; the schema editor writes
// that declaration after each change and the editor asks the server to create
// what a graph declares when it opens and when a declaration changes.
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
const {
  schemaWidget, declaredSchema, sameDeclaration, declarationSignature, changedStores, ensureNotices,
} = await load("storeSchema.ts");
const { onBody } = await load("dynamicPorts.ts");

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- fixtures: the Database def as /api/object_info serves it, and a plain node
const SCHEMA_WIDGET = {
  name: "schema", kind: "schema", default: [], options: [], label: "Schema",
  surface: "hidden", promotable: false,
};
const DATABASE = {
  id: "core.store.database", name: "Database", kind: "store", pulled: true, category: "Store",
  version: "0.1.0", summary: "", colors: {}, inputs: [], outputs: [{ name: "db", type: "db" }],
  widgets: [SCHEMA_WIDGET],
};
const TEXT = {
  id: "core.value.text", name: "Text", kind: "value", pulled: true, category: "Values",
  version: "0.1.0", summary: "", colors: {}, inputs: [], outputs: [{ name: "out", type: "text" }],
  widgets: [{ name: "text", kind: "code", default: "", options: [], label: "Text", surface: "body" }],
};
const defs = new Map([[DATABASE.id, DATABASE], [TEXT.id, TEXT]]);

const CHAT = [{ name: "chat_history", columns: [
  { name: "id", type: "INTEGER", pk: true },
  { name: "time", type: "TEXT", pk: false },
]}];

// ---- the declaration lives in a widget found by kind, never by node id
check("the Database keeps its declaration in its schema widget", schemaWidget(DATABASE)?.name, "schema");
check("a node without a schema widget declares nothing", schemaWidget(TEXT), undefined);
check("an unknown def declares nothing", schemaWidget(undefined), undefined);

// ---- a hidden widget never draws on the body; body is the default surface
check("a hidden widget is not a body knob", onBody(SCHEMA_WIDGET), false);
check("a modal widget is not a body knob", onBody({ surface: "modal" }), false);
check("a body widget is a body knob", onBody({ surface: "body" }), true);
check("no surface means the body", onBody({}), true);

// ---- a live schema becomes a declaration without its row counts
const live = [{ name: "chat_history", rows: 12, columns: [
  { name: "id", type: "INTEGER", pk: true },
  { name: "time", type: "TEXT", pk: false },
]}];
check("declaredSchema drops the row counts", declaredSchema(live), CHAT);
check("the same tables are the same declaration", sameDeclaration(CHAT, declaredSchema(live)), true);
check("new rows change no declaration",
  sameDeclaration(CHAT, declaredSchema([{ ...live[0], rows: 99 }])), true);
check("an added column changes the declaration",
  sameDeclaration(CHAT, declaredSchema([{ ...live[0], columns: [...live[0].columns, { name: "x", type: "TEXT", pk: false }] }])), false);
check("a node that never declared reads as none", sameDeclaration(undefined, []), true);
check("a first table is a change from none", sameDeclaration(undefined, CHAT), false);

// ---- the signature that re-runs the ensure: only declaring nodes count
const node = (id, typeId, config) => ({ id, data: { typeId, config } });
const base = [node("database", DATABASE.id, { db_key: "chat", schema: CHAT }), node("t", TEXT.id, { text: "hi" })];
const sig = declarationSignature(base, defs);
check("a graph that declares tables has a signature", sig.length > 0, true);
check("editing another node keeps the signature",
  declarationSignature([base[0], node("t", TEXT.id, { text: "bye" })], defs), sig);
check("a new column changes the signature",
  declarationSignature([node("database", DATABASE.id, { db_key: "chat", schema: [...CHAT, { name: "x", columns: [] }] }), base[1]], defs) === sig, false);
check("a duplicated Database (fresh key) changes the signature",
  declarationSignature([...base, node("database-2", DATABASE.id, { db_key: "fresh", schema: CHAT })], defs) === sig, false);
check("an empty declaration is no signature",
  declarationSignature([node("database", DATABASE.id, { db_key: "chat", schema: [] })], defs), "");
check("a graph without store nodes has no signature", declarationSignature([base[1]], defs), "");

// ---- what an ensure answer means for the editor
const result = {
  stores: [
    { node: "database", key: "chat", created: ["chat_history"], added: [] },
    { node: "notes", key: "n1", created: [], added: ["notes.body"] },
    { node: "same", key: "s1", created: [], added: [] },
  ],
  warnings: [{ node: "database", message: "chat_history.time is declared text but the database holds it as integer" }],
};
check("stores that gained tables or columns tell their pickers", changedStores(result), ["chat", "n1"]);
check("a conflict is one console line naming the node", ensureNotices(result), [
  "database: chat_history.time is declared text but the database holds it as integer",
]);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall store-schema checks passed");
