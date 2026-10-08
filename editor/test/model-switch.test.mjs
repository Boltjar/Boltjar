// ============================================================================
// Framework-free harness for what a MODEL SWITCH does to a model-driven node
// (LLM / TTS / STT): the config it takes and which of its wires survive. Drives
// the REAL modelSwitchConfig / survivesReshape / concreteOutputs
// (src/lib/dynamicPorts.ts), typesCompatible (src/lib/types.ts) and
// declaredOption (src/lib/knobOptions.ts), each transpiled with the installed
// TypeScript compiler (their only imports are type-only). Run: `npm run test`.
//
// Guards a model switch on a TTS saved with Fish params: a TTS on fish/s2 with
// saved {voice, format} is switched to xai/tts. The Fish voice must not survive
// the switch, and the TTS `text` / `lang` wires must not be cut by it.
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
const { modelSwitchConfig, survivesReshape, concreteInputs, concreteOutputs, promotedParamType } = await load("dynamicPorts.ts");
const { typesCompatible } = await load("types.ts");
const { declaredOption } = await load("knobOptions.ts");

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- fixtures: the node defs + manifests as /api/object_info and /api/models serve them
const port = (name, type, extra = {}) => ({
  name, type, growable: false, optional: false, trigger: false, ...extra,
});
const modelWidget = (dflt) => ({ name: "model", kind: "model", default: dflt, options: [], label: "Model" });
const TTS = {
  id: "core.ai.tts", name: "TTS", kind: "transform", pulled: false, category: "AI",
  version: "0.1.0", summary: "", colors: {}, widgets: [modelWidget("")],
  inputs: [port("trigger", "event", { trigger: true }), port("text", "text"),
           port("lang", "lang", { optional: true })],
  outputs: [port("audio", "audio"), port("trigger", "event")],
};
const LLM = {
  id: "core.ai.llm", name: "LLM", kind: "transform", pulled: false, category: "AI",
  version: "0.1.0", summary: "", colors: {}, widgets: [modelWidget("")],
  inputs: [port("trigger", "event", { trigger: true }), port("prompt", "text"),
           port("tools", "tool", { growable: true, optional: true })],
  outputs: [port("response", "text"), port("reasoning", "text", { optional: true }),
            port("trigger", "event"), port("error", "event", { optional: true })],
};
const param = (name, type, dflt, options = []) => ({
  name, type, default: dflt, min: null, max: null, step: null, options, label: name,
});
const manifest = (id, kind, extra) => ({
  id, kind, provider: id.split("/")[0], model: id.split("/")[1], label: id, summary: "",
  context: 0, inputs: ["text"], outputs: ["text"], tools: false, thinking: false,
  thinking_style: "", json: false, params: [], ...extra,
});
const FISH = manifest("fish/s2", "tts", {
  outputs: ["audio"],
  params: [param("voice", "text", ""), param("format", "select", "wav", ["wav", "mp3", "opus"]),
           param("chunk_length", "int", 100)],
});
const XAI_TTS = manifest("xai/tts", "tts", {
  outputs: ["audio"],
  params: [param("voice", "select", "eve", ["eve", "leo", "rex"]),
           param("language", "select", "auto", ["auto", "en", "fr"]),
           param("speed", "float", 1.0), param("codec", "select", "mp3", ["mp3", "wav"]),
           param("sample_rate", "select", 44100, [24000, 44100])],
});
const GROK_43 = manifest("xai/grok-4.3", "llm", {
  inputs: ["text", "image"], tools: true, thinking: true, thinking_style: "effort", json: true,
  params: [param("temperature", "float", 0.9), param("think", "select", "low", ["none", "low", "medium", "high"]),
           param("json", "bool", false)],
});
const GROK_420 = manifest("xai/grok-4.20", "llm", {
  inputs: ["text", "image"], tools: true, json: true,
  params: [param("temperature", "float", 0.9), param("json", "bool", false)],
});
const PLAIN = manifest("acme/plain", "llm", { params: [param("max_tokens", "int", 1024)] });
const models = new Map([FISH, XAI_TTS, GROK_43, GROK_420, PLAIN].map((m) => [m.id, m]));

const survives = (def, next, connected, wire) =>
  survivesReshape(def, next, new Set(connected), wire, models, typesCompatible);

// ---- 1. the switch starts from the new manifest's defaults: nothing carries over
const fishTts = { model: "fish/s2", params: { voice: "fedcba9876543210fedcba9876543210", format: "wav" }, promoted: [] };
const toXai = modelSwitchConfig(TTS, fishTts, "xai/tts", XAI_TTS);
check("fish/s2 -> xai/tts: model is the new id", toXai.model, "xai/tts");
check("fish/s2 -> xai/tts: params are exactly the xai/tts defaults",
  toXai.params, { voice: "eve", language: "auto", speed: 1.0, codec: "mp3", sample_rate: 44100 });
check("the switch leaves the saved config untouched (no mutation)", fishTts.params.voice, "fedcba9876543210fedcba9876543210");
const unloaded = modelSwitchConfig(TTS, fishTts, "xai/tts", undefined);
check("manifest not loaded yet: params start empty (the backend applies defaults)", unloaded.params, {});

const grok = { model: "xai/grok-4.3", params: { temperature: 0.5, think: "high" }, promoted: ["temperature", "think"] };
const toGrok420 = modelSwitchConfig(LLM, grok, "xai/grok-4.20", GROK_420);
check("grok-4.3 -> grok-4.20: the stale `think` is gone from params", Object.keys(toGrok420.params), ["temperature", "json"]);
check("grok-4.3 -> grok-4.20: only promotions the new model has survive", toGrok420.promoted, ["temperature"]);

// ---- 2. TTS wires: a switch must never cut text / lang / trigger (the old hand list did)
const ttsIn = ["trigger", "text", "lang"];
check("TTS keeps its trigger wire", survives(TTS, toXai, ttsIn, { side: "in", handle: "trigger", type: "event" }), true);
check("TTS keeps its text wire", survives(TTS, toXai, ttsIn, { side: "in", handle: "text", type: "text" }), true);
check("TTS keeps its lang wire (STT.lang)", survives(TTS, toXai, ttsIn, { side: "in", handle: "lang", type: "lang" }), true);
check("TTS keeps its audio output", survives(TTS, toXai, ttsIn, { side: "out", handle: "audio" }), true);
check("TTS keeps its trigger output", survives(TTS, toXai, ttsIn, { side: "out", handle: "trigger" }), true);

// ---- 3. LLM wires follow the new model's ports
const llmIn = ["trigger", "prompt", "image", "tool0", "temperature"];
const toPlain = modelSwitchConfig(LLM, grok, "acme/plain", PLAIN);
check("-> text-only: prompt kept", survives(LLM, toPlain, llmIn, { side: "in", handle: "prompt", type: "text" }), true);
check("-> text-only: image wire pruned", survives(LLM, toPlain, llmIn, { side: "in", handle: "image", type: "image" }), false);
check("-> text-only: tool wire pruned", survives(LLM, toPlain, llmIn, { side: "in", handle: "tool0", type: "tool-call" }), false);
check("-> text-only: promoted param it lacks pruned", survives(LLM, toPlain, llmIn, { side: "in", handle: "temperature", type: "float" }), false);
check("-> text-only: tool_call output pruned", survives(LLM, toPlain, llmIn, { side: "out", handle: "tool_call" }), false);
check("-> text-only: error output kept", survives(LLM, toPlain, llmIn, { side: "out", handle: "error" }), true);
check("-> text-only: response output kept", survives(LLM, toPlain, llmIn, { side: "out", handle: "response" }), true);
check("-> grok-4.20: image wire kept (it reads images)", survives(LLM, toGrok420, llmIn, { side: "in", handle: "image", type: "image" }), true);
check("-> grok-4.20: tool wire kept", survives(LLM, toGrok420, llmIn, { side: "in", handle: "tool0", type: "tool-call" }), true);
check("-> grok-4.20: promoted temperature kept", survives(LLM, toGrok420, llmIn, { side: "in", handle: "temperature", type: "float" }), true);
check("-> grok-4.20 (no thinking): reasoning output pruned", survives(LLM, toGrok420, llmIn, { side: "out", handle: "reasoning" }), false);
// an image wire on a model that has tools but no image input must not be
// re-read as a tool socket just because the tools base accepts any name.
const imageless = manifest("acme/tooler", "llm", { tools: true });
models.set(imageless.id, imageless);
const toTooler = modelSwitchConfig(LLM, grok, "acme/tooler", imageless);
check("-> tools but no image: image wire pruned (not a tool)", survives(LLM, toTooler, llmIn, { side: "in", handle: "image", type: "image" }), false);

// ---- 4. the LLM's error branch renders on every model
const outNames = (cfg) => concreteOutputs(LLM, cfg, models).map((p) => `${p.name}:${p.type}`);
check("grok-4.20 outputs end with the error event", outNames({ model: "xai/grok-4.20" }).includes("error:event"), true);
check("text-only outputs carry the error event", outNames({ model: "acme/plain" }).includes("error:event"), true);
check("no model: the declared error event still renders", outNames({}).includes("error:event"), true);

// ---- 5. a select knob hands back the DECLARED option (an int stays an int)
check("int option: '44100' -> 44100", declaredOption([24000, 44100], "44100"), 44100);
check("string option unchanged", declaredOption(["mp3", "wav"], "wav"), "wav");
check("{value,label} option -> its value", declaredOption([{ value: "\n", label: "new line" }], "\n"), "\n");
check("no matching option: the text passes through", declaredOption([1, 2], "3"), "3");

// ---- 6. a model setting converted to an input is a typed port on any model node
const ins = (def, cfg) => concreteInputs(def, cfg, new Set(), models)
  .filter((p) => !p.ghost).map((p) => `${p.name}:${p.type}${p.promotedParam ? " (converted)" : ""}`);
check("TTS on xai/tts: its converted speed is a float input",
  ins(TTS, { model: "xai/tts", promoted: ["speed"] }), ["trigger:event", "text:text", "lang:lang", "speed:float (converted)"]);
check("TTS on fish/s2: its converted chunk_length is an int input",
  ins(TTS, { model: "fish/s2", promoted: ["chunk_length"] }),
  ["trigger:event", "text:text", "lang:lang", "chunk_length:int (converted)"]);
check("a setting the picked model lacks mints no port",
  ins(TTS, { model: "xai/tts", promoted: ["chunk_length"] }), ["trigger:event", "text:text", "lang:lang"]);
check("a select setting carries text", promotedParamType(TTS, { model: "fish/s2", promoted: ["format"] }, "format", models), "text");
check("a setting not converted has no port", promotedParamType(TTS, { promoted: [] }, "speed", models), undefined);
check("no model picked: a converted setting mints no port",
  ins(TTS, { promoted: ["speed"] }), ["trigger:event", "text:text", "lang:lang"]);
const ttsSpeed = { model: "xai/tts", promoted: ["speed"] };
check("xai/tts -> fish/s2: the speed wire is pruned (fish has no speed)",
  survives(TTS, modelSwitchConfig(TTS, ttsSpeed, "fish/s2", FISH), [...ttsIn, "speed"],
    { side: "in", handle: "speed", type: "float" }), false);
check("xai/tts kept: the speed wire survives",
  survives(TTS, modelSwitchConfig(TTS, ttsSpeed, "xai/tts", XAI_TTS), [...ttsIn, "speed"],
    { side: "in", handle: "speed", type: "float" }), true);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall model-switch checks passed");
