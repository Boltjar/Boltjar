// ============================================================================
// Framework-free harness for the node look: the header glyph (nodeIcon in
// src/lib/kinds.ts) and the subline + body summary (src/lib/nodeMeta.ts), both
// read from what a node declares on its @node decorator, never from a table
// keyed by node id. Drives the REAL modules, transpiled with the installed
// TypeScript compiler; a relative import between lib modules is loaded the
// same way. Run from editor/: `node test/node-look.test.mjs`.
//
// fixtures/core-nodes.json is the served definition of every core node (the
// Python suite keeps it equal to the registry). The *_BEFORE tables are what
// the editor drew for each core node before the declarations existed, taken
// from the per-id tables this change removed: the look must not move.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const lib = resolve(here, "../src/lib");
const loaded = new Map();
/** Transpile a lib module; its relative imports load the same way first. */
async function load(rel) {
  if (loaded.has(rel)) return loaded.get(rel);
  let js = ts.transpileModule(readFileSync(resolve(lib, rel), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  for (const m of [...js.matchAll(/from "\.\/([\w]+)"/g)]) {
    const dep = await load(`${m[1]}.ts`);
    js = js.replace(m[0], `from "${dep.url}"`);
  }
  const url = "data:text/javascript," + encodeURIComponent(js);
  const entry = { url, mod: await import(url) };
  loaded.set(rel, entry);
  return entry;
}
const { nodeIcon, kindStyle } = (await load("kinds.ts")).mod;
const { headerSubline, renderSubline, bodySummary, sublineFields } = (await load("nodeMeta.ts")).mod;

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// the icon names the editor ships: the keys of the Icon registry in icons.tsx
const iconSource = readFileSync(resolve(lib, "icons.tsx"), "utf8");
const registry = iconSource.slice(iconSource.indexOf("const REGISTRY"));
const SHIPPED = new Set([
  ...[...registry.matchAll(/^\s*"?([a-z0-9-]+)"?\s*:\s*[A-Z]\w+,/gm)].map((m) => m[1]),
  ...[...iconSource.matchAll(/REGISTRY\["([^"]+)"\]/g)].map((m) => m[1]),
]);
const known = (name) => SHIPPED.has(name);

const defs = JSON.parse(readFileSync(resolve(here, "fixtures/core-nodes.json"), "utf8"));
const byId = new Map(defs.map((d) => [d.id, d]));

const ICON_BEFORE = {
  "core.value.text": "text-outline",
  "core.value.integer": "calculator-outline",
  "core.value.float": "calculator-outline",
  "core.value.boolean": "toggle-outline",
  "core.value.image": "code-slash-outline",
  "core.value.audio": "code-slash-outline",
  "core.parse.tags": "git-compare-outline",
  "core.text.strip": "git-compare-outline",
  "core.sensor.screen": "time-outline",
  "core.sensor.window": "time-outline",
  "core.sensor.foreground": "time-outline",
  "core.sensor.clock": "time-outline",
  "core.trigger.interval": "timer-outline",
  "core.trigger.schedule": "flash",
  "core.trigger.manual": "play-circle-outline",
  "core.trigger.chat": "chatbubble-ellipses-outline",
  "core.trigger.audio_in": "flash",
  "core.trigger.webhook": "flash",
  "core.data.template": "document-text-outline",
  "core.data.format_list": "git-compare-outline",
  "core.data.list": "git-compare-outline",
  "core.data.compute": "calculator-outline",
  "core.logic.condition": "git-branch-outline",
  "core.ai.llm": "sparkles-outline",
  "core.ai.tool": "git-compare-outline",
  "core.ai.tool_args": "git-compare-outline",
  "core.ai.stt": "mic-outline",
  "core.ai.tts": "volume-high-outline",
  "core.ai.embed": "git-compare-outline",
  "core.ai.rerank": "git-compare-outline",
  "core.data.chunk": "git-compare-outline",
  "core.data.sentences": "git-compare-outline",
  "core.store.vectors": "albums-outline",
  "core.vectors": "git-compare-outline",
  "core.store.database": "albums-outline",
  "core.db": "git-compare-outline",
  "core.data.parse": "git-compare-outline",
  "core.data.stringify": "git-compare-outline",
  "core.data.build": "git-compare-outline",
  "core.data.get": "git-compare-outline",
  "core.data.split": "git-compare-outline",
  "core.file.read": "document-text-outline",
  "core.file.write": "create-outline",
  "core.file.append": "add-circle-outline",
  "core.file.delete": "trash-outline",
  "core.file.list": "folder-open-outline",
  "core.store.kv": "albums-outline",
  "core.kv": "git-compare-outline",
  "core.state.meter": "git-compare-outline",
  "core.trigger.agenda": "flash",
  "core.net.http": "git-compare-outline",
  "core.output.log": "terminal-outline",
  "core.output.preview": "eye-outline",
  "core.flow.wireless_in": "git-compare-outline",
  "core.flow.wireless_out": "git-compare-outline",
  "core.flow.router": "git-compare-outline",
  "core.flow.for_each": "git-compare-outline",
  "core.flow.changed": "git-compare-outline",
  "core.flow.sync": "git-compare-outline",
  "core.flow.wait": "git-compare-outline",
  "core.flow.queue": "git-compare-outline",
  "core.output.chat": "chatbubbles-outline",
  "core.output.avatar": "exit-outline",
};

const SUBLINE_BEFORE = [
  ["core.value.text", {}, "text · empty"],
  ["core.value.text", {"text":""}, "text · empty"],
  ["core.value.text", {"text":"Hi"}, "text · Hi"],
  ["core.value.text", {"text":"Hello there,\n  this is a long block of text"}, "text · Hello there, th…"],
  ["core.value.integer", {}, "int · 0"],
  ["core.value.integer", {"number":5}, "int · 5"],
  ["core.value.integer", {"number":-12}, "int · -12"],
  ["core.value.float", {}, "float · 0"],
  ["core.value.float", {"number":2.5}, "float · 2.5"],
  ["core.value.float", {"number":0}, "float · 0"],
  ["core.value.boolean", {}, "bool · false"],
  ["core.value.boolean", {"on":true}, "bool · true"],
  ["core.value.boolean", {"on":false}, "bool · false"],
  ["core.value.image", {}, "values"],
  ["core.value.audio", {}, "values"],
  ["core.parse.tags", {}, "data"],
  ["core.text.strip", {}, "text"],
  ["core.sensor.screen", {}, "sensors"],
  ["core.sensor.window", {}, "sensors"],
  ["core.sensor.foreground", {}, "sensors"],
  ["core.sensor.clock", {}, "clock · volatile"],
  ["core.trigger.interval", {}, "every · 2s"],
  ["core.trigger.interval", {"seconds":60}, "every · 60s"],
  ["core.trigger.interval", {"seconds":0.5}, "every · 0.5s"],
  ["core.trigger.schedule", {}, "triggers"],
  ["core.trigger.manual", {}, "manual · once"],
  ["core.trigger.chat", {}, "chat · on send"],
  ["core.trigger.audio_in", {}, "triggers"],
  ["core.trigger.webhook", {}, "triggers"],
  ["core.data.template", {}, "template · 1 tag"],
  ["core.data.template", {"template":"{a} and {b}"}, "template · 2 tags"],
  ["core.data.template", {"template":"{a}"}, "template · 1 tag"],
  ["core.data.template", {"template":"no tags"}, "template · 0 tags"],
  ["core.data.template", {"template":"{trigger} {x}"}, "template · 1 tag"],
  ["core.data.format_list", {}, "data"],
  ["core.data.list", {}, "data"],
  ["core.data.compute", {}, "compute · value"],
  ["core.data.compute", {"expression":"value * 2 + offset_long_name"}, "compute · value * 2 + o…"],
  ["core.data.compute", {"expression":"x"}, "compute · x"],
  ["core.logic.condition", {}, "route · value"],
  ["core.logic.condition", {"expression":"len(value) > 3 and value != 'stop'"}, "route · len(value) > …"],
  ["core.ai.llm", {}, "model · "],
  ["core.ai.llm", {"model":"xai/grok-4.20"}, "xai · grok-4.20"],
  ["core.ai.llm", {"model":"gemma"}, "model · gemma"],
  ["core.ai.tool", {}, "ai"],
  ["core.ai.tool_args", {}, "ai"],
  ["core.ai.stt", {}, "fish · asr"],
  ["core.ai.stt", {"model":"xai/stt"}, "xai · stt"],
  ["core.ai.stt", {"model":""}, "fish · asr"],
  ["core.ai.tts", {}, "xai · tts"],
  ["core.ai.tts", {"model":"fish/s2"}, "fish · s2"],
  ["core.ai.tts", {"model":""}, "xai · tts"],
  ["core.ai.embed", {}, "ai"],
  ["core.ai.rerank", {}, "ai"],
  ["core.data.chunk", {}, "data"],
  ["core.data.sentences", {}, "data"],
  ["core.store.vectors", {}, "store"],
  ["core.vectors", {}, "store"],
  ["core.store.database", {}, "store"],
  ["core.db", {}, "store"],
  ["core.data.parse", {}, "data"],
  ["core.data.stringify", {}, "data"],
  ["core.data.build", {}, "data"],
  ["core.data.get", {}, "data"],
  ["core.data.split", {}, "data"],
  ["core.file.read", {}, "files"],
  ["core.file.write", {}, "files"],
  ["core.file.append", {}, "files"],
  ["core.file.delete", {}, "files"],
  ["core.file.list", {}, "files"],
  ["core.store.kv", {}, "store"],
  ["core.kv", {}, "store"],
  ["core.state.meter", {}, "state"],
  ["core.trigger.agenda", {}, "triggers"],
  ["core.net.http", {}, "network"],
  ["core.output.log", {}, "log · log"],
  ["core.output.log", {"label":"a much longer log label"}, "log · a much longer…"],
  ["core.output.log", {"label":"x"}, "log · x"],
  ["core.output.preview", {}, "preview · live tap"],
  ["core.flow.wireless_in", {}, "flow"],
  ["core.flow.wireless_out", {}, "flow"],
  ["core.flow.router", {}, "flow"],
  ["core.flow.for_each", {}, "flow"],
  ["core.flow.changed", {}, "flow"],
  ["core.flow.sync", {}, "flow"],
  ["core.flow.wait", {}, "flow"],
  ["core.flow.queue", {}, "flow"],
  ["core.output.chat", {}, "inspect"],
  ["core.output.avatar", {}, "output"],
];

const BODY_BEFORE = [
  ["core.value.text", {"text":"promoted text value that is long"}, [{"key":"Text","value":"promoted text value that is…"}]],
  ["core.value.integer", {}, []],
  ["core.value.float", {}, []],
  ["core.value.boolean", {}, []],
  ["core.value.image", {}, []],
  ["core.value.audio", {}, []],
  ["core.parse.tags", {}, []],
  ["core.text.strip", {}, []],
  ["core.sensor.screen", {}, []],
  ["core.sensor.window", {}, []],
  ["core.sensor.foreground", {}, []],
  ["core.sensor.clock", {}, []],
  ["core.trigger.interval", {"seconds":5}, []],
  ["core.trigger.schedule", {}, []],
  ["core.trigger.manual", {}, []],
  ["core.trigger.chat", {}, []],
  ["core.trigger.audio_in", {}, []],
  ["core.trigger.webhook", {}, []],
  ["core.data.template", {}, [{"key":"Template","value":"{in}"}]],
  ["core.data.format_list", {}, []],
  ["core.data.list", {}, []],
  ["core.data.compute", {"expression":"value * 2"}, [{"key":"Expression","value":"value * 2"}]],
  ["core.data.compute", {}, [{"key":"Expression","value":"value"}]],
  ["core.logic.condition", {"expression":"value > 1"}, [{"key":"Expression","value":"value > 1"}]],
  ["core.ai.llm", {}, []],
  ["core.ai.tool", {}, []],
  ["core.ai.tool_args", {}, []],
  ["core.ai.stt", {}, []],
  ["core.ai.tts", {}, []],
  ["core.ai.embed", {}, []],
  ["core.ai.rerank", {}, []],
  ["core.data.chunk", {}, []],
  ["core.data.sentences", {}, []],
  ["core.store.vectors", {}, []],
  ["core.vectors", {}, []],
  ["core.store.database", {}, []],
  ["core.db", {}, []],
  ["core.data.parse", {}, []],
  ["core.data.stringify", {}, []],
  ["core.data.build", {}, []],
  ["core.data.get", {}, []],
  ["core.data.split", {}, []],
  ["core.file.read", {}, []],
  ["core.file.write", {}, []],
  ["core.file.append", {}, []],
  ["core.file.delete", {}, []],
  ["core.file.list", {}, []],
  ["core.store.kv", {}, []],
  ["core.kv", {}, []],
  ["core.state.meter", {}, []],
  ["core.trigger.agenda", {}, []],
  ["core.net.http", {}, []],
  ["core.output.log", {"label":"x"}, []],
  ["core.output.preview", {}, []],
  ["core.flow.wireless_in", {}, []],
  ["core.flow.wireless_out", {}, []],
  ["core.flow.router", {}, []],
  ["core.flow.for_each", {}, []],
  ["core.flow.changed", {}, []],
  ["core.flow.sync", {}, []],
  ["core.flow.wait", {}, []],
  ["core.flow.queue", {}, []],
  ["core.output.chat", {}, []],
  ["core.output.avatar", {}, []],
];

// ---- every core node draws the glyph it drew before, and a real one
check("the snapshot and the before table list the same core nodes",
  [...byId.keys()].sort(), Object.keys(ICON_BEFORE).sort());
for (const d of defs) {
  check(`${d.id} draws ${ICON_BEFORE[d.id]}`, nodeIcon(d, known), ICON_BEFORE[d.id]);
  check(`${d.id} draws an icon the editor ships`, known(nodeIcon(d, known)), true);
}

// ---- a pack node's icon: declared, unknown, or none
const pack = (extra) => ({ id: "pack.x", kind: "service", category: "Pack", widgets: [], inputs: [], ...extra });
check("a pack node draws the icon it declares", nodeIcon(pack({ icon: "globe-outline" }), known), "globe-outline");
check("a pack icon the editor does not ship draws the kind glyph",
  nodeIcon(pack({ icon: "rocket-outline" }), known), kindStyle("service").icon);
check("a pack node that declares none draws the kind glyph", nodeIcon(pack({}), known), kindStyle("service").icon);

// ---- every core subline reads as it did before
for (const [id, config, want] of SUBLINE_BEFORE) {
  check(`${id} ${JSON.stringify(config)} reads "${want}"`, headerSubline(byId.get(id), config), want);
}

// ---- the body summary rows (shown when every knob is promoted) are unchanged
for (const [id, config, want] of BODY_BEFORE) {
  check(`${id} ${JSON.stringify(config)} body rows`, bodySummary(byId.get(id), config), want);
}

// ---- the subline filters, one by one
const W = (name, kind, dflt) => ({ name, kind, label: name, default: dflt });
const node = (subline, widgets, inputs = []) => ({ id: "pack.x", kind: "transform", category: "Pack", subline, widgets, inputs });
check("text outside a placeholder is kept", renderSubline(node("fetch · once", []), {}), "fetch · once");
check("an unset field reads as its default", renderSubline(node("{n}", [W("n", "number", 4)]), {}), "4");
check("a set field wins over its default", renderSubline(node("{n}", [W("n", "number", 4)]), { n: 0 }), "0");
check("clip cuts to one short line", renderSubline(node("{t|clip:6}", [W("t", "code", "")]), { t: "a\nlong line" }), "a lon…");
check("clip without a length cuts at 22",
  renderSubline(node("{t|clip}", [W("t", "code", "")]), { t: "x".repeat(30) }), "x".repeat(21) + "…");
check("or fills an empty value", renderSubline(node("{t|or:none}", [W("t", "text", "")]), {}), "none");
check("or keeps a value", renderSubline(node("{t|or:none}", [W("t", "text", "")]), { t: "a" }), "a");
check("bool reads truth", renderSubline(node("{b|bool}", [W("b", "bool", false)]), { b: 1 }), "true");
check("model splits provider and model", renderSubline(node("{m|model}", [W("m", "model", "")]), { m: "xai/tts" }), "xai · tts");
check("model with no provider", renderSubline(node("{m|model}", [W("m", "model", "")]), { m: "echo" }), "model · echo");
check("an empty model reads as the default one",
  renderSubline(node("{m|model}", [W("m", "model", "fish/asr")]), { m: "" }), "fish · asr");
check("tags counts the {tags} of a template",
  renderSubline(node("{t|tags}", [W("t", "code", "")]), { t: "{a} {b} {a}" }), "2 tags");
check("tags leaves out a name a declared port owns",
  renderSubline(node("{t|tags}", [W("t", "code", "")], [{ name: "trigger", growable: false }]), { t: "{trigger} {a}" }), "1 tag");
check("an unknown filter passes the value on", renderSubline(node("{t|shout}", [W("t", "text", "")]), { t: "a" }), "a");
check("a node without a subline shows its category", headerSubline(node("", []), {}), "pack");
check("the subline fields, in order", sublineFields(node("{a} · {b|clip:3} · {a}", [])), ["a", "b", "a"]);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall node look checks passed");
