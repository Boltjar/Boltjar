// ============================================================================
// Framework-free test script for the model picker's decisions: which models it
// lists (the runnable ones of the node's family), how it groups them (by
// provider, in the served order), and what its "last updated" line and its
// button say. Drives the REAL src/lib/modelMeta.ts, transpiled with the
// installed TypeScript compiler (its only imports are type-only). Run:
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
const {
  AUTO_MODEL, ago, listState, modelStatus, pickerGroups, runnableModels, searchModels, updatedLine,
} = await load("modelMeta.ts");

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- fixtures: rows as GET /api/models serves them (merged, in served order)
const model = (id, extra = {}) => ({
  id, provider: id.split("/")[0], model: id.slice(id.indexOf("/") + 1), label: id,
  summary: "", context: 0, kind: "llm", inputs: ["text"], outputs: ["text"], tools: false,
  thinking: false, thinking_style: "", json: false, params: [], available: true,
  source: "discovered", ...extra,
});
const served = [
  model("ollama/gemma4:e4b", { label: "Gemma 4 e4b (Ollama)", source: "both" }),
  model("ollama/llama3.2:latest"),
  model("ollama/qwen3:14b", { available: false, reason: "not installed in Ollama: pull it in Connections", source: "manifest" }),
  model("ollama/nomic-embed-text:latest", { kind: "embed" }),
  model("anthropic/claude-opus-5", { label: "Claude Opus 5" }),
  model("xai/grok-4.7", { summary: "Listed by xAI" }),
  model("xai/tts", { kind: "tts", source: "manifest" }),
  model("fish/s2", { kind: "tts", available: false, reason: "no Fish Audio key: add it in Connections", source: "manifest" }),
  // an older TOML with no kind is an LLM.
  { ...model("pack/legacy"), kind: undefined },
];

// ---- the list: runnable models of the node's family only
check("an LLM picker lists runnable chat models, never an unavailable one",
  runnableModels(served, "llm").map((m) => m.id),
  ["ollama/gemma4:e4b", "ollama/llama3.2:latest", "anthropic/claude-opus-5", "xai/grok-4.7", "pack/legacy"]);
check("a TTS picker lists runnable TTS models only",
  runnableModels(served, "tts").map((m) => m.id), ["xai/tts"]);
check("an embed picker lists embed models",
  runnableModels(served, "embed").map((m) => m.id), ["ollama/nomic-embed-text:latest"]);

// ---- grouping: by provider, groups and rows in the served order
check("groups keep the served provider order",
  pickerGroups(served, "llm", "").map((g) => [g.provider, g.models.map((m) => m.id)]),
  [["ollama", ["ollama/gemma4:e4b", "ollama/llama3.2:latest"]],
   ["anthropic", ["anthropic/claude-opus-5"]],
   ["xai", ["xai/grok-4.7"]],
   ["pack", ["pack/legacy"]]]);
check("a search narrows the groups (label, id, provider or summary)",
  pickerGroups(served, "llm", "GROK").map((g) => g.provider), ["xai"]);
check("a search by summary", searchModels(served, "listed by").map((m) => m.id), ["xai/grok-4.7"]);
check("a search that matches nothing runnable gives no group",
  pickerGroups(served, "llm", "qwen3:14b"), []);
check("an empty search keeps every row", searchModels(served, "  ").length, served.length);

// ---- the "last updated" line
const now = Date.parse("2026-09-27T12:00:00Z");
const meta = (extra = {}) => ({
  list: "ready", auto: null, updated: null, refreshing: false, providers: {}, ...extra,
});
check("never refreshed", updatedLine(meta(), now), "not checked yet");
check("refreshing", updatedLine(meta({ refreshing: true, updated: "2026-09-27T11:59:00Z" }), now), "refreshing…");
check("seconds read as just now", updatedLine(meta({ updated: "2026-09-27T11:59:30Z" }), now), "updated just now");
check("minutes", updatedLine(meta({ updated: "2026-09-27T11:55:00Z" }), now), "updated 5 min ago");
check("hours", ago("2026-09-27T09:00:00Z", now), "3 h ago");
check("days", ago("2026-09-24T12:00:00Z", now), "3 days ago");
const listing = (extra = {}) => ({
  ok: true, checked: "2026-09-27T11:55:00Z", updated: "2026-09-27T11:55:00Z", error: null,
  failure: null, ...extra,
});
check("an Ollama that never answered is a setup without it, not news",
  updatedLine(meta({
    updated: "2026-09-27T11:55:00Z",
    providers: { ollama: listing({ ok: false, updated: null, error: "refused", failure: "not_running" }),
                 xai: listing() },
  }), now),
  "updated 5 min ago");
check("an Ollama that answered before and stopped is not running",
  updatedLine(meta({
    updated: "2026-09-27T11:55:00Z",
    providers: { ollama: listing({ ok: false, updated: "2026-09-27T09:00:00Z", failure: "not_running" }) },
  }), now),
  "updated 5 min ago · Ollama not running");
check("a refused key reads as refused, not unreachable",
  updatedLine(meta({
    updated: "2026-09-27T11:55:00Z",
    providers: { ollama: listing(),
                 xai: listing({ ok: false, updated: null, error: "xAI 401", failure: "key_refused" }),
                 anthropic: listing({ ok: false, updated: null, failure: "timeout" }),
                 groq: listing({ ok: false, updated: null, failure: "unreachable" }) },
  }), now),
  "updated 5 min ago · xAI refused the key, Anthropic timed out, Groq unreachable");
check("asked but nothing ever answered: checked, not 'not checked yet'",
  updatedLine(meta({
    providers: { ollama: listing({ ok: false, checked: "2026-09-27T11:58:00Z", updated: null, failure: "not_running" }),
                 lmstudio: listing({ ok: false, checked: "2026-09-27T11:58:00Z", updated: null, failure: "not_running" }) },
  }), now),
  "checked 2 min ago · Lmstudio not running");
check("a failure of no known kind (an older cache) failed",
  updatedLine(meta({
    updated: "2026-09-27T11:55:00Z",
    providers: { xai: listing({ ok: false, failure: undefined }) },
  }), now),
  "updated 5 min ago · xAI failed");

// ---- what the button says about the picked model
const byId = new Map(served.map((m) => [m.id, m]));
check("nothing picked", modelStatus("", byId, meta()), { state: "none", note: "" });
check("auto names the model it runs",
  modelStatus(AUTO_MODEL, byId, meta({ auto: "ollama/gemma4:e4b" })), { state: "auto", note: "runs Gemma 4 e4b (Ollama)" });
check("auto with nothing connected runs the mock",
  modelStatus(AUTO_MODEL, byId, meta()), { state: "auto", note: "runs the mock until a model is connected" });
check("an unavailable model carries the server's reason",
  modelStatus("ollama/qwen3:14b", byId, meta()),
  { state: "unavailable", note: "not installed in Ollama: pull it in Connections" });
check("a vanished model is missing",
  modelStatus("ollama/llama3.1:8b", byId, meta()), { state: "missing", note: "not in the model list any more" });
check("a runnable model is ok", modelStatus("xai/grok-4.7", byId, meta()), { state: "ok", note: "" });
check("the offline mock is never missing",
  modelStatus("mock/echo", byId, meta()), { state: "ok", note: "" });

// ---- before a list was read, nothing is called missing
check("the first read in flight is loading", listState(false, true, null), "loading");
check("a first read that failed is failed", listState(false, false, "models 500"), "failed");
check("a list read once stays ready when a later read fails", listState(true, false, "models 500"), "ready");
const empty = new Map();
check("while the list loads a picked model is neutral",
  modelStatus("xai/grok-4.7", empty, meta({ list: "loading" })), { state: "unknown", note: "" });
check("a list that could not be read claims nothing about the model",
  modelStatus("xai/grok-4.7", empty, meta({ list: "failed" })),
  { state: "unknown", note: "the model list could not be read" });
check("auto while the list loads says what it does, not that it runs the mock",
  modelStatus(AUTO_MODEL, empty, meta({ list: "loading" })),
  { state: "auto", note: "picks a model that can run when it runs" });
check("the mock is fine before the list too",
  modelStatus("mock/echo", empty, meta({ list: "failed" })), { state: "ok", note: "" });

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall model picker checks passed");
