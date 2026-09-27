// ============================================================================
// Backend protocol types: the contract with boltjar.server (Python/FastAPI).
// Mirrors boltjar/sdk.py (Port, Widget, NodeSpec.definition) and the Graph
// shape that GET/PUT /api/graphs and the /ws power loop exchange. The editor is
// the client; Vite proxies /api and /ws.
// ============================================================================

/** A node kind. Drives the header colour and the fallback identity glyph (lib/kinds.ts). */
export type NodeKind =
  | "value"
  | "trigger"
  | "sensor"
  | "transform"
  | "logic"
  | "service"
  | "store"
  | "output"
  | "subgraph";

/** A config field rendered as an inspector / inline control. */
export type WidgetKind =
  | "text"
  | "number"
  | "bool"
  | "select"
  | "code"
  | "secret"
  | "color"
  /** A model picker: the LLM node's capability-driven model selector. */
  | "model"
  /** A store's declared schema (tables + columns); kept by the node's schema
   *  editor, never drawn as a knob (its surface is "hidden"). */
  | "schema";

/** A typed input or output socket on a node definition. */
export interface Port {
  name: string;
  type: string;
  /** A growable input materialises one real socket per wire (named), plus a ghost. */
  growable: boolean;
  optional: boolean;
  /** A triggering input fires the node; non-trigger inputs latch (data, pulled). */
  trigger: boolean;
  /** op-shaping: show this port only when config[op_field] is in op_values. */
  op_field?: string | null;
  op_values?: string[];
  /** a growable port whose minted sockets are meaningful fields/columns. */
  ghost_base?: string | null;
  /** an output port that offers a one-click "scaffold" gesture: spawn this node
   *  type and pre-wire the port into it (the body-builder affordance). Read
   *  generically, no per-node-id branch. e.g. Tool.call -> core.ai.tool_args. */
  scaffold?: string | null;
}

/** A config field declared by a node, rendered on the node body (or a modal).
 *  Its behaviour is declared, not guessed by id. */
export interface Widget {
  name: string;
  kind: WidgetKind;
  default: unknown;
  options: unknown[];
  label: string;
  /** numeric widgets: when min+max are present the editor draws a slider. */
  min?: number | null;
  max?: number | null;
  step?: number | null;
  /** "body" (inline knob) | "modal" (opens the shared modal) | "hidden" (saved
   *  with the graph, never drawn: a value one of the node's surfaces keeps). */
  surface?: "body" | "modal" | "hidden";
  /** offer right-click "Convert to input" (promote knob to a typed port). */
  promotable?: boolean;
  /** the input port's type when this widget is promoted. */
  port_type?: string;
  /** value carries {tag} pipes substituted from wired inputs. */
  template?: boolean;
  /** value may reference {{secret.NAME}} (autocomplete on `{{`). */
  accepts_secrets?: boolean;
  /** op-shaping: visible only when config[op_field] is in op_values. */
  op_field?: string | null;
  op_values?: string[];
  /** example shown when the field is empty. */
  placeholder?: string;
  /** live dropdown source: "db.tables" | "kv.keys"; options come from the wired store. */
  options_from?: string | null;
  /** a code/text field that grows vertically when the node is resized (others stay
   *  fixed). A node with >=1 expandable field is resizable. */
  expand?: boolean;
  /** a model picker (kind "model") lists the models of this family: llm | tts |
   *  stt | embed | rerank. Its `options` are the special values it offers above
   *  the list ("auto" on the LLM). null on every other widget. */
  model_kind?: string | null;
}

/** A registered node definition from GET /api/object_info. */
export interface NodeDef {
  id: string;
  name: string;
  kind: NodeKind;
  /** A pure/volatile data node: evaluated on demand (pulled), never self-fires. */
  pulled: boolean;
  category: string;
  version: string;
  summary: string;
  inputs: Port[];
  outputs: Port[];
  widgets: Widget[];
  /** port name -> seed colour hex (the design layer overrides via TYPE_COLORS). */
  colors: Record<string, string>;
  /** passthrough shape {inputPort: outputPort}: when disabled, the runtime wires
   *  through (source of inputPort -> consumers of outputPort) instead of cutting. */
  bypass?: Record<string, string>;
  /** the Ionicons name the node declares for itself; empty: its kind glyph. */
  icon?: string;
  /** the header subline template: text with {field|filter} placeholders
   *  (lib/nodeMeta renderSubline); empty: the category in lower case. */
  subline?: string;
}

/** GET /api/object_info payload. */
export interface ObjectInfo {
  nodes: NodeDef[];
  /** type name -> colour hex. */
  types: Record<string, string>;
}

// ----------------------------------------------------------------- model registry

/** One configurable parameter of a model (a knob, promotable to a typed input). */
export interface ModelParam {
  name: string;
  /** float | int | text | bool | select: drives both the knob and the port type. */
  type: "float" | "int" | "text" | "bool" | "select";
  default: unknown;
  min: number | null;
  max: number | null;
  step: number | null;
  options: unknown[];
  label: string;
}

/**
 * A concrete model and everything the LLM node needs to reshape to it. `inputs`
 * and `outputs` are the MODALITIES the model accepts/emits (both always include
 * "text"); `tools` flags tool-calling; `params` are the knobs.
 */
export interface ModelManifest {
  id: string;
  provider: string;
  model: string;
  label: string;
  summary: string;
  context: number;
  /** the node family this manifest serves: llm | tts | stt | embed | rerank. The
   *  picker filters by this so each model node only lists its own family. */
  kind?: "llm" | "tts" | "stt" | "embed" | "rerank";
  inputs: string[];
  outputs: string[];
  tools: boolean;
  /** runnable now: its provider has a key / answers, and lists it. */
  available?: boolean;
  /** why it is not runnable (only when available is false). */
  reason?: string;
  /** where it comes from: a TOML manifest, a provider's live list, or both. */
  source?: "manifest" | "discovered" | "both";
  /** other names its provider takes for it; `<provider>/<alias>` resolves to it. */
  aliases?: string[];
  /** the model can reason; when true the LLM node grows a `reasoning` output. */
  thinking: boolean;
  /** how the thinking lever reshapes: effort | level | budget | token | bool. */
  thinking_style: string;
  /** the model can be constrained to JSON output (drives the json knob). */
  json: boolean;
  params: ModelParam[];
}

/** One provider's last answer to "which models do you have?". Times are ISO
 *  8601 UTC. */
export interface ProviderListing {
  ok: boolean;
  checked: string;
  updated: string | null;
  error: string | null;
  /** what kind of failure the last attempt was (null when it answered). */
  failure?: ProviderFailure | null;
  count: number;
}

/** not_running: a server on this computer refused the connection; unreachable:
 *  a remote one did; key_refused: a 401 or 403; error: anything else. */
export type ProviderFailure = "not_running" | "unreachable" | "timeout" | "key_refused" | "error";

/** GET /api/models (and POST /api/models/refresh) payload: the live list. */
export interface ModelsInfo {
  models: ModelManifest[];
  /** the model an "auto" LLM runs now; null means the offline mock. */
  auto?: string | null;
  /** the newest successful refresh (ISO 8601 UTC); null before the first. */
  updated?: string | null;
  /** a background refresh is running (the list will change soon). */
  refreshing?: boolean;
  providers?: Record<string, ProviderListing>;
}

// --------------------------------------------------------------- db schema

/** One column in a Database node's table, as served by GET /api/db/{key}/schema. */
export interface DbColumn {
  name: string;
  type: string;
  pk: boolean;
}

/** One table: its columns plus a live row count. */
export interface DbTable {
  name: string;
  rows: number;
  columns: DbColumn[];
}

/** The schema endpoint envelope: a list of tables (each mutation echoes this). */
export interface DbSchemaInfo {
  schema: DbTable[];
}

// --------------------------------------------------------------------- graph

/** A node instance placed on the canvas. `type` is the NodeDef id. */
export interface GraphNode {
  id: string;
  type: string;
  config: Record<string, unknown>;
  pos: [number, number];
  /** persisted [width, height] for resizable nodes (text blocks); optional. */
  size?: [number, number];
  /** bypassed: the node is skipped by both validation and the runtime. */
  disabled?: boolean;
}

/** A directed connection from one node's output port to another's input port. */
export interface GraphEdge {
  src: string;
  src_port: string;
  dst: string;
  dst_port: string;
}

/** A visual group boxing a set of nodes (editor-only; the runtime ignores it).
 *  Its rectangle is derived from the members' bounding box, so it auto-resizes. */
export interface NodeGroup {
  id: string;
  title: string;
  /** a palette key (see GROUP_COLORS) tinting the box + bar. */
  color: string;
  /** member node ids. */
  members: string[];
}

/** The serialised graph exchanged with the backend. */
export interface Graph {
  /** the saved-graph format version (boltjar/graph_format.py). The server
   *  migrates what it loads to its current format and stamps it on save. */
  format?: number;
  name?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** visual node groups (editor-only; never reaches the runtime). */
  groups?: NodeGroup[];
}

// ----------------------------------------------------------------- validation

/** One pre-run validation problem from POST /api/validate (and ws `invalid`). */
export interface Problem {
  /** the offending node id, or null for graph-level problems (e.g. no trigger). */
  node: string | null;
  kind: string;
  message: string;
}

// ------------------------------------------------------------------ ws events

/** A single value emitted on a port during a live run (lights ports + wires). */
export interface ValueEvent {
  kind: "value";
  node: string;
  port: string;
  value: unknown;
}

export interface LogEvent {
  kind: "log";
  node: string;
  message: string;
}

/** Something the runtime could not make match but runs anyway, e.g. a declared
 *  store column whose live type differs (power-on still goes ahead). */
export interface WarningEvent {
  kind: "warning";
  node: string;
  message: string;
}

/** The runtime power state (replaces the old run-once `running` flag). */
export interface StatusEvent {
  kind: "status";
  power: "on" | "off";
}

/** On was refused because the graph is invalid; the editor badges the nodes.
 *  `resume`: the refusal came from a launch powering back a graph that was On
 *  ("Resume workflows after launch"); the server replays it on connect until
 *  the graph turns On or a person turns it Off. */
export interface InvalidEvent {
  kind: "invalid";
  problems: Problem[];
  resume?: boolean;
}

export interface NodeErrorEvent {
  kind: "node_error";
  node: string;
  error: string;
}

/** A node's live work status: `running` for the full duration of a fire (so the
 *  editor shows exactly what is working now), then `ok` when it settles. */
export interface NodeStatusEvent {
  kind: "node_status";
  node: string;
  status: "idle" | "running" | "ok" | "warn" | "error";
}

export interface ErrorEvent {
  kind: "error";
  error: string;
  /** a launch powering back a graph that was On failed to build it */
  resume?: boolean;
}

/** The set of nodes/edges the live runtime was built from: the source of truth
 *  for what is LIVE. Sent on every power change and replayed on connect. The
 *  editor classifies each node/wire against it so live values only paint/pulse
 *  through the running graph, never a draft-only structural edit made since the
 *  last Save & Restart. `edges` are [src, src_port, dst, dst_port] tuples; empty
 *  lists mean nothing is live. */
export interface LiveGraphEvent {
  kind: "live_graph";
  nodes: string[];
  edges: [string, string, string, string][];
}

/** A tool hop the model made: which Tool node, with what args (`tool_call`) and
 *  the value handed back (`tool_result`). Surfaced in the console so the agentic
 *  loop is visible hop by hop. */
export interface ToolCallEvent {
  kind: "tool_call";
  node: string;
  args: unknown;
}
export interface ToolResultEvent {
  kind: "tool_result";
  node: string;
  result: unknown;
}

/** Any event the runtime streams over /ws. */
export type RunEvent =
  | ValueEvent
  | LogEvent
  | WarningEvent
  | StatusEvent
  | InvalidEvent
  | NodeErrorEvent
  | NodeStatusEvent
  | ErrorEvent
  | ToolCallEvent
  | ToolResultEvent
  | LiveGraphEvent;

/** A client->server command over /ws. */
export type RunCommand =
  | { action: "on"; graph: Graph }
  | { action: "off" }
  | { action: "restart"; graph: Graph }
  | { action: "chat"; node: string; text: string }
  | { action: "audio"; node: string; audio: string; lang?: string }
  | { action: "fire"; node: string };
