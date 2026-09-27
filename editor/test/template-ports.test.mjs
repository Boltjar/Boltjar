// ============================================================================
// Framework-free harness for the Template node's concrete ports. Drives the
// REAL concreteInputs / concreteOutputs / templateTags / ghostSocketName
// (src/lib/dynamicPorts.ts), transpiled with the installed TypeScript compiler
// (its only imports are type-only). Run: `npm run test`.
//
// The Template declares a `trigger` input (it fires the node, so it must be
// wired) and a `trigger` output (Chat History -> Template -> LLM: assemble,
// then pass the trigger on). Both render as declared ports; `trigger` is
// never a {tag}, so a `{trigger}` in the string, a trigger wire, or a source
// node named "trigger" dropped on the tag ghost can never mint a tag socket
// that shadows the declared port.
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
const { concreteInputs, concreteOutputs, templateTags, ghostSocketName, sourceSocketSlug } = await load("dynamicPorts.ts");

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- fixture: the Template def exactly as /api/object_info serves it
const port = (name, type, extra = {}) => ({
  name, type, growable: false, optional: false, trigger: false, ...extra,
});
const TEMPLATE = {
  id: "core.data.template", name: "Template", kind: "transform", pulled: false, category: "Data",
  version: "0.1.0", summary: "", colors: {},
  widgets: [{ name: "template", kind: "code", default: "{in}", options: [], label: "Template" }],
  inputs: [port("trigger", "event", { trigger: true }),
           port("tag", "any", { growable: true })],
  outputs: [port("out", "text"), port("trigger", "event")],
};

const ins = (cfg, connected = []) => concreteInputs(TEMPLATE, cfg, new Set(connected));
const names = (ports) => ports.map((p) => p.name);
const trig = (ports) => ports.filter((p) => p.name === "trigger");

// ---- 1. the declared trigger input renders, then one socket per {tag}, then the ghost
const plain = ins({ template: "{persona} :: {msg}" }, ["trigger", "persona"]);
check("inputs: trigger, the tags in template order, the ghost", names(plain), ["trigger", "persona", "msg", "tag·+"]);
check("the trigger input is the declared event trigger (not a tag)",
  trig(plain).map((p) => [p.type, p.trigger, p.optional, p.dynamic, !!p.stale]), [["event", true, false, false, false]]);

// ---- 2. `{trigger}` in the string is literal text, never a tag socket
const literal = ins({ template: "{trigger} stays :: {msg}" }, ["trigger", "msg"]);
check("{trigger} in the string mints no tag socket", names(literal), ["trigger", "msg", "tag·+"]);
check("exactly one trigger input, the declared one", trig(literal).map((p) => p.dynamic), [false]);
check("templateTags drops a declared port name", templateTags(TEMPLATE, "{trigger} {msg} {msg} {tag}"), ["msg", "tag"]);

// ---- 3. a wired trigger never reads as a stale (deleted) tag
check("a trigger wire on a tagless template is not stale",
  ins({ template: "no tags" }, ["trigger"]).map((p) => `${p.name}${p.stale ? ":stale" : ""}`), ["trigger", "tag·+"]);
check("a wire whose tag left the string still reads stale",
  ins({ template: "no tags" }, ["old"]).map((p) => `${p.name}${p.stale ? ":stale" : ""}`), ["trigger", "old:stale", "tag·+"]);

// ---- 4. outputs: the assembled text and the passed-on trigger
check("outputs: out (text) + trigger (event)",
  concreteOutputs(TEMPLATE, {}).map((p) => `${p.name}:${p.type}`), ["out:text", "trigger:event"]);

// ---- 5. a ghost drop names the socket after its source, never a declared port
check("a source named after a declared port gets a fresh name", ghostSocketName(TEMPLATE, "trigger", new Set()), "trigger2");
check("an ordinary source keeps its slug", ghostSocketName(TEMPLATE, "persona", new Set()), "persona");
check("a slug already used on the node is numbered", ghostSocketName(TEMPLATE, "persona", new Set(["persona"])), "persona2");
check("the growable base name itself stays usable", ghostSocketName(TEMPLATE, "tag", new Set()), "tag");

// ---- 6. the tag a dropped wire mints carries the source's WHOLE name
check("a multi-word node keeps every word", sourceSocketSlug(TEMPLATE, "User message", "out"), "user_message");
check("punctuation and case fold into one slug", sourceSocketSlug(TEMPLATE, "Chat Append (User)", "out"), "chat_append_user");
{
  // "Username" and "User message" wired onto one Template never share a tag
  const first = ghostSocketName(TEMPLATE, sourceSocketSlug(TEMPLATE, "Username", "out"), new Set());
  const second = ghostSocketName(TEMPLATE, sourceSocketSlug(TEMPLATE, "User message", "out"), new Set([first]));
  check("two user-ish sources get two tags", [first, second], ["username", "user_message"]);
}

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall template-port checks passed");
