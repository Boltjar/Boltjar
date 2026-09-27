// ============================================================================
// Framework-free harness for the ⌘K palette's node search (src/lib/nodeSearch.ts):
// the Add node rows are ranked by where the query lands (exact name, name
// prefix, a word in the name, name, id, category, summary) and cut to the row
// limit after ranking. Drives the REAL module, transpiled with the installed
// TypeScript compiler (it has no imports). Run from editor/: `node test/node-search.test.mjs`.
//
// The nodes below are real core definitions, in the order the registry serves
// them. The reported case: typing "LLM" put Screen Capture first (its summary
// mentions a vision LLM and it is served before the LLM node), so Enter added
// the wrong node.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/nodeSearch.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { matchRank, rankNodes } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const node = (id, name, category, summary) => ({ id, name, category, summary });
const REGISTRY = [
  node("core.value.text", "Text", "Values", "A constant text block."),
  node("core.sensor.screen", "Screen Capture", "Sensors", "Grab a fresh screenshot on every pull (a live frame for a vision LLM). Cross-platform."),
  node("core.trigger.chat", "Chat Input", "Triggers", "Type a message and send it into the live graph."),
  node("core.data.template", "Template", "Data", "Assemble text from {tag} pipes. Each {tag} is a wired input. Pulled by default; wire the optional `trigger` to assemble on a fire, then pass the trigger on (out first, then trigger)."),
  node("core.ai.llm", "LLM", "AI", "A chat / multimodal model. Reshapes its inputs, outputs, and params to the capabilities of the selected model. A provider failure fires `error` (with the message) instead of a reply."),
  node("core.ai.tool", "Tool", "AI", "A tool the LLM can call. It declares a name, a description and its parameters. When the model calls it, `call` carries the arguments into the body you wired, and the answer that comes back on `result` goes back to the model."),
  node("core.ai.stt", "STT", "AI", "Speech to text. Picks a model from any connected STT provider the same way the LLM picks its model. `lang` carries the language the provider detected."),
  node("core.ai.tts", "TTS", "AI", "Text to speech. Picks a model from any connected TTS provider the same way the LLM picks its model."),
  node("core.ai.embed", "Embed", "AI", "Encode text into an embedding vector. Picks an embed model (Ollama bge-m3 by default) the way the LLM picks its model. Fires on its trigger and emits the vector + a trigger to sequence the next node."),
  node("core.ai.rerank", "Rerank", "AI", "A cross-encoder precision pass: score each candidate against the query and keep the best. Picks a rerank model (an HTTP service at the `url` knob)."),
  node("core.data.sentences", "Sentences", "Data", "Split text into a list of sentences (each ends on . ! ? ... or a new line). Feed it through For-each to send an LLM reply to TTS one sentence at a time, so speech starts sooner."),
  node("core.store.vectors", "Vector Store", "Store", "An embedded vector store (sqlite + cosine). Emits a handle the Vectors node indexes into and searches."),
  node("core.vectors", "Vectors", "Store", "Index and search an embedded vector store. The operation knob reshapes the knobs, inputs and outputs."),
  node("core.store.database", "Database", "Store", "An embedded SQLite database. Emits a db connection other nodes use."),
  node("core.db", "DB", "Store", "Read and write a SQLite store. The operation knob reshapes the knobs and outputs. An insert or update stores a data: URL value as text."),
  node("core.file.read", "Read File", "Files", "Read a file's text from the sandbox. Pulled, fresh; a missing file reads as empty."),
  node("core.file.write", "Write File", "Files", "Write text to a file in the sandbox on a trigger, creating parent dirs. Emits the resolved relative path."),
  node("core.file.append", "Append File", "Files", "Append text to a file in the sandbox on a trigger, creating it if absent. Emits the resolved relative path."),
  node("core.file.delete", "Delete File", "Files", "Delete a file in the sandbox on a trigger (idempotent; a missing file is a no-op). Emits the resolved relative path."),
  node("core.file.list", "List Dir", "Files", "List entry names under a directory in the sandbox. Pulled, fresh; a missing dir lists as []. Default path is the sandbox root."),
  node("core.store.kv", "KV Store", "Store", "A persistent key-value store. Emits a kv handle other nodes read and write through."),
  node("core.kv", "KV", "Store", "Read/write a kv store. The 'operation' knob reshapes the inputs and outputs."),
  node("core.net.http", "HTTP Request", "Network", "Call an external API. Fires on a trigger; resolves {{secret.NAME}} in url / headers / body and substitutes {tag} pipes from wired inputs."),
  node("core.flow.for_each", "For-each", "Flow", "Dispense a list one item at a time. Fire `trigger` to start; wire the body's done back into `loop` to release the next; `after_last` fires when the list is exhausted."),
  node("core.flow.changed", "Changed", "Flow", "Pass a value on only when it differs from the last one (a dedup gate). Interval, HTTP Request, Changed and LLM in a row poll a source and run the LLM only on a real change."),
  node("core.flow.queue", "Queue", "Flow", "Run many producers through one lane. Each arrival on `in` waits in a buffer, `out` releases one item, and the next goes only when `ack` fires, so two turns reach a shared LLM one after the other."),
  node("core.output.chat", "Chat", "Inspect", "Show the running conversation: your message and the model's reply. A live viewer, like Preview, that keeps history."),
];
const names = (rows) => rows.map((d) => d.name);
const PALETTE_ROWS = 8;

// what the palette did before: a substring over every field, in registry
// order, cut to eight rows before anything was ranked
const before = (q) => REGISTRY
  .filter((d) => `${d.name} ${d.id} ${d.category} ${d.summary}`.toLowerCase().includes(q.toLowerCase()))
  .slice(0, PALETTE_ROWS);
check("the fixture reproduces the report: LLM used to list Screen Capture first",
  names(before("LLM"))[0], "Screen Capture");

check("LLM: the LLM node comes first", names(rankNodes(REGISTRY, "LLM", PALETTE_ROWS))[0], "LLM");
check("llm in lower case reads the same", names(rankNodes(REGISTRY, "llm", PALETTE_ROWS))[0], "LLM");
check("LLM: nodes that only mention it follow in registry order",
  names(rankNodes(REGISTRY, "LLM", PALETTE_ROWS)),
  ["LLM", "Screen Capture", "Tool", "STT", "TTS", "Embed", "Sentences", "Changed"]);

check("db: DB, then Database, above the nodes that only mention it",
  names(rankNodes(REGISTRY, "db", PALETTE_ROWS)).slice(0, 2), ["DB", "Database"]);
check("db: a word in a summary ranks above letters inside a word (sandbox)",
  names(rankNodes(REGISTRY, "db", PALETTE_ROWS)),
  ["DB", "Database", "Read File", "Write File", "Append File", "Delete File", "List Dir"]);

check("a name prefix ranks above a category match",
  names(rankNodes(REGISTRY, "data", PALETTE_ROWS)).slice(0, 3), ["Database", "Template", "Sentences"]);
check("a word in the name: file finds every file node first",
  names(rankNodes(REGISTRY, "file", PALETTE_ROWS)).slice(0, 4), ["Read File", "Write File", "Append File", "Delete File"]);
check("a hyphen splits words: each finds For-each before the summaries saying each",
  names(rankNodes(REGISTRY, "each", PALETTE_ROWS))[0], "For-each");
check("nodes of one rank keep the registry order",
  names(rankNodes(REGISTRY, "vector", PALETTE_ROWS)).slice(0, 2), ["Vector Store", "Vectors"]);
check("an id match ranks above a category match",
  names(rankNodes(REGISTRY, "for_each", PALETTE_ROWS)), ["For-each"]);
check("a category match ranks above a summary match",
  names(rankNodes(REGISTRY, "inspect", PALETTE_ROWS)), ["Chat"]);

// the served definitions (fixtures/core-nodes.json, kept equal to the registry
// by tests/test_node_look.py): "sql" used to add Vector Store, whose summary
// said "sqlite", ahead of Database and DB, all three tied on a summary word
const SERVED = JSON.parse(readFileSync(resolve(here, "fixtures/core-nodes.json"), "utf8"));
check("sql, over the served nodes: Database and DB first",
  names(rankNodes(SERVED, "sql", PALETTE_ROWS)).slice(0, 2), ["Database", "DB"]);
check("sql: Vector Store is not offered for it", names(rankNodes(SERVED, "sql", PALETTE_ROWS)).includes("Vector Store"), false);
check("LLM, over the served nodes: the LLM node first", names(rankNodes(SERVED, "LLM", PALETTE_ROWS))[0], "LLM");

check("the row limit cuts after ranking: one row is the best match",
  names(rankNodes(REGISTRY, "LLM", 1)), ["LLM"]);
check("an empty query lists the registry in order, cut to the limit",
  names(rankNodes(REGISTRY, "", 3)), ["Text", "Screen Capture", "Chat Input"]);
check("no match lists nothing", rankNodes(REGISTRY, "zzz", PALETTE_ROWS), []);
check("a query across fields still finds its node, last",
  names(rankNodes(REGISTRY, "llm core.ai", PALETTE_ROWS)), ["LLM"]);

// the ranks themselves, one per tier
const llm = REGISTRY.find((d) => d.id === "core.ai.llm");
const kv = REGISTRY.find((d) => d.id === "core.store.kv");
check("rank: exact name", matchRank(llm, "  LLM "), 0);
check("rank: name prefix", matchRank(kv, "kv s"), 1);
check("rank: word prefix in the name", matchRank(kv, "store"), 2);
check("rank: name contains", matchRank(kv, "tor"), 3);
check("rank: id", matchRank(kv, "store.kv"), 4);
check("rank: word in the summary", matchRank(kv, "persistent"), 6);
check("rank: summary contains", matchRank(kv, "ersist"), 7);
check("rank: nowhere", matchRank(kv, "vision"), null);
check("every match the palette found before is still found",
  REGISTRY.every((d) => ["llm", "db", "data", "file", "sql", "a"].every((q) =>
    before(q).includes(d) ? matchRank(d, q) !== null : true)), true);

if (failures) {
  console.error(`\n${failures} node search check(s) failed`);
  process.exit(1);
}
console.log("\nall node search checks passed");
