// ============================================================================
// Framework-free checks of the {tag} autocomplete list (tagSuggestions in
// src/lib/dynamicPorts.ts, whose only imports are type-only), transpiled with
// the installed TypeScript compiler. Run from editor/: `node test/tag-suggestions.test.mjs`.
//
// The case it guards: a Template fed by a node named "Instructions" offered
// both {instructions} (its wire's socket, the tag that works) and
// {Instructions} (the raw node name, a tag that fills nothing). A tag is the
// source's whole name slugified, so the list has one entry per source.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/dynamicPorts.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { tagSuggestions, sourceSocketSlug } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// the Template's declared ports, as the server describes them
const TEMPLATE = {
  id: "core.data.template",
  inputs: [
    { name: "trigger", type: "event", trigger: true },
    { name: "tag", type: "any", growable: true },
  ],
  outputs: [],
  widgets: [],
};
// a wire dropped on the tag ghost is named after its source's slug
const dropped = (source) => ({ src: source, dstPort: sourceSocketSlug(TEMPLATE, source, "text") });

check("a source named Instructions is offered once, as its slug",
  tagSuggestions(TEMPLATE, [dropped("Instructions")], ""), ["instructions"]);
check("a source whose name has spaces is offered as its whole slug",
  tagSuggestions(TEMPLATE, [dropped("User message")], ""), ["user_message"]);
check("the tag in the text is not offered again in another case",
  tagSuggestions(TEMPLATE, [dropped("Instructions")], "Do this: {instructions} {Instructions}"), ["instructions"]);
check("one entry per source, wired order, then the text's other tags",
  tagSuggestions(TEMPLATE, [dropped("Chat"), dropped("Clock"), dropped("Chat")], "{clock} {mood}"),
  ["chat", "clock", "mood"]);
check("a wire on a named socket offers that socket's tag, not the source name",
  tagSuggestions(TEMPLATE, [{ src: "Persona", dstPort: "instructions" }], ""), ["instructions"]);
check("the trigger wire and a ghost placeholder offer no tag",
  tagSuggestions(TEMPLATE, [{ src: "Chat", dstPort: "trigger" }, { src: "Chat", dstPort: "tag·+" }], ""), []);
check("a {trigger} in the text is never a tag",
  tagSuggestions(TEMPLATE, [], "{trigger} {name}"), ["name"]);

if (failures) {
  console.error(`\n${failures} failing check(s)`);
  process.exit(1);
}
console.log("\nall tag suggestion checks passed");
