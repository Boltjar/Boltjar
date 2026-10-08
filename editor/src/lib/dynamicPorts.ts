// ============================================================================
// Dynamic (growable) port model: the structural core of the editor.
//
// The runtime keys every edge by (dst, dst_port) and treats an edge whose
// dst_port is NOT a declared input as a *dynamic* port (boltjar.runtime
// `_all_inputs`). So a growable port does not render as `name#0, name#1`; it
// materialises into **named** sockets, one per wire, and the name is meaningful:
//
//   • Template: each `{tag}` in the `template` string is its own input port,
//     named by the tag (the headline intelligence feature). Wiring a node into
//     `persona` fills `{persona}`.
//   • Compute / LLM tools / generic growable: the materialised ports are the
//     distinct dst_port names of the edges already landing on that growable
//     base, plus one trailing ghost "add" socket.
//
// This module is the single place that knows how a node's *concrete* input
// ports are derived from (NodeDef + config + incoming edges). The renderer, the
// inspector, the connection validator and the tag autocomplete all read it.
// ============================================================================
import type { ModelManifest, ModelParam, NodeDef, Port, Widget, WidgetKind } from "../types/protocol";

/** The Template node id; its growable port is driven by the template string. */
export const TEMPLATE_ID = "core.data.template";
/** The Template's text widget, which the node's own text editor draws. */
export const TEMPLATE_TEXT = "template";

/** The consolidated KV node id; the 'operation' knob reshapes its visible
 *  knobs + output ports. Knobs are templates ({tag} from wired inputs,
 *  {{secret.X}} resolves a secret), mirroring HTTP. */
export const KV_ID = "core.kv";

/** The consolidated DB node id; same template + operation-reshape contract as KV. */
export const DB_ID = "core.db";

/** A knob's value as the node runs with it: the saved value, else (the key is
 *  absent or null) the widget's declared default. A node saved untouched
 *  (`config: {}`, normal for graphs written through the API or MCP) shows its
 *  defaults, and the backend merges them the same way (runtime.node_config). */
export function configValue(
  widgets: readonly { name: string; default?: unknown }[],
  config: Record<string, unknown>,
  name: string,
): unknown {
  return config[name] ?? widgets.find((w) => w.name === name)?.default;
}

/** Op-shaping, read from the declaration (no per-id table): a widget or port is
 *  visible when it declares no op_field, or when the operation (configValue:
 *  the saved value, else the knob's default) is one of its op_values. Used for
 *  the inline knobs and the input and output reshape, so all three agree. */
export function opVisible(
  d: { op_field?: string | null; op_values?: string[] },
  config: Record<string, unknown>,
  widgets: readonly { name: string; default?: unknown }[],
): boolean {
  if (!d.op_field) return true;
  const current = String(configValue(widgets, config, d.op_field) ?? "");
  return (d.op_values ?? []).includes(current);
}

/** Whether a widget draws as an inline knob on the node body: its surface is
 *  "body" (the default), not "modal" (the shared modal) nor "hidden" (a value
 *  the node keeps with the graph, such as a store's declared schema). */
export function onBody(w: { surface?: string }): boolean {
  return (w.surface ?? "body") === "body";
}

/** The widgets a node draws as knob rows on its body: each body widget shown
 *  for the current operation and not promoted to a port. A node with a surface
 *  of its own draws its knob rows too, under the surface, so Chat Input's
 *  placeholder and a custom model node's own knobs are on the canvas like any
 *  knob. Left out is what a surface already draws: the model picker draws the
 *  model widget (kind "model"), and `drawnBySurface` names any widget another
 *  surface edits. */
export function knobRowWidgets(
  def: NodeDef,
  config: Record<string, unknown>,
  drawnBySurface: readonly string[] = [],
): Widget[] {
  const promoted = new Set(nodePromoted(config));
  return def.widgets.filter((w) =>
    onBody(w) && w.kind !== "model" && !drawnBySurface.includes(w.name)
    && opVisible(w, config, def.widgets) && !promoted.has(w.name));
}

/** The Build JSON node id; each wired named input becomes a key in the json object. */
export const BUILD_ID = "core.data.build";

/** The Split JSON node id; its `keys` config mints one output port per key. */
export const SPLIT_ID = "core.data.split";

/** Tool Args: splits a tool `call` into one output per declared argument (the
 *  same names that generate the tool's schema). Reshapes exactly like Split JSON. */
export const TOOL_ARGS_ID = "core.ai.tool_args";

/** Wireless In/Out: a virtual wire by channel. The Out's outputs MIRROR the
 *  sockets wired into the In on the same channel (computed from the graph). */
export const WIRELESS_IN_ID = "core.flow.wireless_in";
export const WIRELESS_OUT_ID = "core.flow.wireless_out";

/** Router: a pure wire reroute (in -> out). The runtime flattens it; its output
 *  resolves to the real upstream source so the wire keeps its name/type. */
export const ROUTER_ID = "core.flow.router";

/** the sockets broadcast on each wireless channel (the In's wired sockets), keyed
 *  by channel: each carries its name + wire type + the REAL upstream source
 *  (src node + port) so the Out can mirror the ports AND displays/edges can read
 *  the real value stream (the runtime emits on the source, not the wireless port). */
export type WirelessSocket = { name: string; type: string; src: string; srcPort: string };
export type WirelessChannelMap = ReadonlyMap<string, ReadonlyArray<WirelessSocket>>;

/** The LLM node id; its concrete ports come from its selected model's manifest. */
export const LLM_ID = "core.ai.llm";
/** The HTTP Request node id; its `tag` growable mirrors Template's, and its
 *  `body` output type is reshaped from the `response_type` widget. */
export const HTTP_ID = "core.net.http";

/** A node's model picker: its first `model` widget, or null for a node without
 *  one. Declared on the widget, never keyed off a node id, so any custom node that
 *  declares a model widget gets the picker and its per-model knobs. */
export function modelWidgetOf(def: NodeDef): Widget | null {
  return def.widgets.find((w) => w.kind === "model") ?? null;
}

/** The manifest family (llm, tts, stt, embed, rerank) a node's model picker
 *  lists, from its widget's `model_kind` (a model widget that declares none
 *  lists LLMs, the manifest default). null for a node with no model picker. */
export function modelKindOf(def: NodeDef): string | null {
  const widget = modelWidgetOf(def);
  return widget ? widget.model_kind || "llm" : null;
}

/** A lookup from a model id to its manifest (the editor context's models map). */
export type ModelLookup = ReadonlyMap<string, ModelManifest>;

/** The LLM's always-present base inputs (before any modality / tools / promoted). */
const LLM_BASE_INPUT_NAMES = new Set(["trigger", "prompt"]);

/** Map a model param's type onto the port type its promoted socket carries. */
export function paramPortType(type: ModelParam["type"]): string {
  switch (type) {
    case "float": return "float";
    case "int": return "int";
    case "bool": return "bool";
    case "select": return "text";
    case "text":
    default: return "text";
  }
}

/** The model a node with a model picker runs: its saved pick, or "" while
 *  none is picked (a model is never chosen for the author). */
export function modelIdOf(def: NodeDef, config: Record<string, unknown>): string {
  const widget = modelWidgetOf(def);
  if (!widget) return "";
  const picked = config[widget.name];
  return typeof picked === "string" ? picked : "";
}

/**
 * The port type of a model setting converted to an input, on any node with a
 * model picker (LLM, TTS, STT, Embed, Rerank, a custom node's own): config.promoted
 * names it and the picked model declares it. undefined otherwise, so a setting
 * of a previously picked model mints no phantom port.
 */
export function promotedParamType(
  def: NodeDef,
  config: Record<string, unknown>,
  handle: string,
  models?: ModelLookup,
): string | undefined {
  if (!models || !nodePromoted(config).includes(handle)) return undefined;
  const param = models.get(modelIdOf(def, config))?.params.find((p) => p.name === handle);
  return param ? paramPortType(param.type) : undefined;
}

/** Read the picked model id from an LLM node's config (config.model); "" when none is picked. */
export function llmModelId(config: Record<string, unknown>): string {
  const id = config.model;
  return typeof id === "string" && id ? id : "";
}

/** The promoted param names on an LLM node (config.promoted), filtered to strings. */
export function llmPromoted(config: Record<string, unknown>): string[] {
  const raw = config.promoted;
  return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : [];
}

/**
 * The promoted WIDGET names on any node (config.promoted). This is the generic
 * form of `llmPromoted` (universal knob promotion): `config.promoted` is a per-node
 * list of widget names the author lifted into typed input ports. Both readers parse
 * the same array; they are kept distinct only so each call site reads intentfully.
 */
export function nodePromoted(config: Record<string, unknown>): string[] {
  const raw = config.promoted;
  return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : [];
}

/** Map a widget's kind onto the port type its promoted input socket carries. */
export function widgetPortType(kind: WidgetKind): string {
  switch (kind) {
    case "number": return "float";
    case "bool": return "bool";
    case "code":
    case "select":
    case "secret":
    case "color":
    case "text":
    default: return "text";
  }
}

/** A concrete input socket on a placed node (declared or materialised). */
export interface ConcretePort {
  /** the literal handle id == the backend dst_port for this socket. */
  name: string;
  type: string;
  trigger: boolean;
  optional: boolean;
  /** true when this socket came from a growable/dynamic base. */
  dynamic: boolean;
  /** the declared growable base port name this socket belongs to (if dynamic). */
  base?: string;
  /** a ghost "add another" socket: not yet wired, accepts a new connection. */
  ghost?: boolean;
  /** a wired tag/port that no longer has a backing source (Template stale tag). */
  stale?: boolean;
  /** an LLM param promoted to a typed input port (offers "Convert to widget"). */
  promotedParam?: boolean;
}

const TAG_RE = /\{([a-zA-Z_][\w]*)\}/g;

/** Parse the ordered, unique `{tag}` names out of a Template string. */
export function parseTemplateTags(template: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  let m: RegExpExecArray | null;
  TAG_RE.lastIndex = 0;
  while ((m = TAG_RE.exec(template)) !== null) {
    const tag = m[1];
    if (!seen.has(tag)) {
      seen.add(tag);
      out.push(tag);
    }
  }
  return out;
}

/** The names a minted socket can never take on this node: its declared, non-
 *  growable inputs (Template's and HTTP's `trigger`). A socket named after one
 *  would shadow the declared port (one handle id, two meanings), so a `{trigger}`
 *  in a Template string stays literal text and a source called "trigger" dropped
 *  on a tag ghost is numbered instead. Read off the def, never a per-id list. */
export function reservedInputNames(def: NodeDef): Set<string> {
  return new Set(def.inputs.filter((p) => !p.growable).map((p) => p.name));
}

/** The `{tag}` sockets a Template string mints: its tags in order, minus any name
 *  a declared port owns (see reservedInputNames). */
export function templateTags(def: NodeDef, template: string): string[] {
  const reserved = reservedInputNames(def);
  return parseTemplateTags(template).filter((tag) => !reserved.has(tag));
}

/** The {tag} autocomplete list of a node whose text carries {tag} pipes (the
 *  Template, an HTTP body): one entry per wired source, the tag that source
 *  fills, which is its wire's socket (named after the source's slug when it was
 *  dropped, see sourceSocketSlug), then the tags already in `text`. Never the
 *  source node's raw name, and never one tag twice in two capitalisations (the
 *  first spelling, the wired one, wins). A wire on a declared port (a trigger)
 *  or on a ghost placeholder fills no tag. */
export function tagSuggestions(
  def: NodeDef,
  wires: readonly { src: string; dstPort: string }[],
  text = "",
): string[] {
  const reserved = reservedInputNames(def);
  const out: string[] = [];
  const seen = new Set<string>();
  const offer = (tag: string) => {
    const k = tag.toLowerCase();
    if (seen.has(k)) return;
    seen.add(k);
    out.push(tag);
  };
  for (const w of wires) {
    if (!w.dstPort || reserved.has(w.dstPort) || w.dstPort.endsWith("·+")) continue;
    offer(w.dstPort);
  }
  for (const tag of templateTags(def, text)) offer(tag);
  return out;
}

/** The socket name a wire dropped on a ghost-named growable base mints: the
 *  wired source's slug, numbered (slug2, slug3...) past any name already `used`
 *  on the node and past the node's declared ports. */
export function ghostSocketName(
  def: NodeDef | undefined,
  slug: string,
  used: ReadonlySet<string>,
): string {
  const taken = new Set([...used, ...(def ? reservedInputNames(def) : [])]);
  let name = slug;
  let i = 2;
  while (taken.has(name)) name = `${slug}${i++}`;
  return name;
}

/** Whether a growable base names each socket it mints after the WIRED SOURCE
 *  (the Template's tags, and any port declaring `ghost_base`: an HTTP/KV/DB tag,
 *  a Build field, a Queue or Wireless In socket) rather than auto-numbering it
 *  (Compute value0, LLM tool0). The Template is the one source-named base that
 *  does not declare it. Shared by the ghost drop and the node rename. */
export function namesSocketsAfterSource(def: NodeDef | undefined, base: string): boolean {
  if (!def) return false;
  return def.id === TEMPLATE_ID || !!def.inputs.find((p) => p.growable && p.name === base)?.ghost_base;
}

/** The name a wire from `source`.`sourcePort` asks for on a source-named base,
 *  before ghostSocketName numbers it: the source's slug, so it is a valid {tag}
 *  token, or `source.port` on a Wireless In, so two ports of one source stay
 *  distinct and self-describing (the {tag} grammar has no dot, and a Wireless
 *  socket is never a token). */
export function sourceSocketSlug(def: NodeDef | undefined, source: string, sourcePort: string): string {
  return def?.id === WIRELESS_IN_ID
    ? `${slugifyTag(source)}.${slugifyTag(sourcePort)}`
    : slugifyTag(source);
}

/** Parse the ordered, unique key list out of a Split JSON `keys` config. Splits on
 *  commas, spaces and newlines, drops blanks, dedupes preserving first-seen order
 *  (mirrors the backend `_split_keys` so the output ports line up exactly). */
export function parseSplitKeys(keys: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const tok of String(keys ?? "").split(/[\s,]+/)) {
    if (tok && !seen.has(tok)) {
      seen.add(tok);
      out.push(tok);
    }
  }
  return out;
}

/** True if this def has at least one growable input port. */
export function hasGrowable(def: NodeDef): boolean {
  return def.inputs.some((p) => p.growable);
}

/**
 * The concrete input ports for a placed node, expanding any growable base into
 * named sockets. `connectedPortNames` is the set of dst_port names that already
 * have a wire into this node (literal handle ids, from the live edge set).
 *
 * For the LLM node the concrete ports come from its selected model's manifest
 * (`models` resolves config.model): the base inputs, then one port per non-text
 * input modality, then the growable `tools` base (if the model tool-calls), then
 * one typed port per promoted param. No model / missing manifest falls back to
 * the declared base ports (so the node still works).
 *
 * A declared input whose op_field names other operations is left out, as an
 * op-shaped output is (a Vectors `embedding` under clear): the backend does not
 * require it there either. The operation is read as the backend reads it
 * (opVisible: the saved value else the knob's declared default).
 */
export function concreteInputs(
  def: NodeDef,
  config: Record<string, unknown>,
  connectedPortNames: ReadonlySet<string>,
  models?: ModelLookup,
): ConcretePort[] {
  const ports = inputsFor(def, config, connectedPortNames, models);
  const hidden = new Set(def.inputs
    .filter((p) => !p.growable && p.op_field && !opVisible(p, config, def.widgets))
    .map((p) => p.name));
  return hidden.size ? ports.filter((p) => p.dynamic || !hidden.has(p.name)) : ports;
}

function inputsFor(
  def: NodeDef,
  config: Record<string, unknown>,
  connectedPortNames: ReadonlySet<string>,
  models?: ModelLookup,
): ConcretePort[] {
  if (def.id === LLM_ID) {
    return llmInputs(def, config, connectedPortNames, models);
  }
  if (def.id === BUILD_ID) {
    return buildInputs(def, config, connectedPortNames);
  }
  // HTTP / KV / DB share the same {tag} growable contract: trigger + the
  // node's static handles + one named socket per wired tag, plus a ghost.
  if (def.id === HTTP_ID || def.id === KV_ID || def.id === DB_ID) {
    return httpInputs(def, config, connectedPortNames);
  }
  const out: ConcretePort[] = [];
  const declared = new Set(def.inputs.map((p) => p.name));
  const promoted = new Set(nodePromoted(config));

  for (const p of def.inputs) {
    if (!p.growable) {
      out.push({
        name: p.name,
        type: p.type,
        trigger: p.trigger,
        optional: p.optional,
        dynamic: false,
      });
      continue;
    }

    // --- a growable base: materialise named sockets ---
    if (def.id === TEMPLATE_ID) {
      const template = String(config.template ?? "");
      // a declared port (the `trigger` that fires the Template) rendered above;
      // its name is never a tag, so `{trigger}` in the string mints no second socket.
      const tags = templateTags(def, template);
      const tagSet = new Set(tags);
      // one socket per tag, in template order; a connected tag shows as filled.
      for (const tag of tags) {
        out.push({
          name: tag,
          type: p.type,
          trigger: false,
          optional: true,
          dynamic: true,
          base: p.name,
        });
      }
      // wires whose tag was deleted from the template: surface as stale so the
      // user can see (and remove) the dangling connection rather than lose it.
      for (const conn of connectedPortNames) {
        if (!tagSet.has(conn) && !declared.has(conn)) {
          out.push({
            name: conn,
            type: p.type,
            trigger: false,
            optional: true,
            dynamic: true,
            base: p.name,
            stale: true,
          });
        }
      }
      // a ghost so a wire can be dropped straight onto the Template: it mints a
      // tag named after the source node and injects {tag} into the string.
      out.push({
        name: ghostHandleId(p.name),
        type: p.type,
        trigger: false,
        optional: true,
        dynamic: true,
        base: p.name,
        ghost: true,
      });
    } else {
      // generic growable (Compute value, Sync / Queue in, …): one socket per
      // existing wire on this base, in a stable order, then a ghost "add" socket.
      // A wired knob promoted to an input is not one of them: it is drawn as
      // that knob's port below (appendPromotedWidgets).
      const mine = [...connectedPortNames]
        .filter((n) => belongsToBase(n, p.name, def, declared, promoted))
        .sort(compareDynamicNames);
      for (const n of mine) {
        out.push({
          name: n,
          type: p.type,
          trigger: p.trigger,
          optional: true,
          dynamic: true,
          base: p.name,
        });
      }
      out.push({
        name: ghostHandleId(p.name),
        type: p.type,
        trigger: p.trigger,
        optional: true,
        dynamic: true,
        base: p.name,
        ghost: true,
      });
    }
  }

  appendPromotedWidgets(def, config, out);
  appendPromotedParams(def, config, out, models);
  return out;
}

/**
 * The model settings converted to inputs on a node with a model picker other
 * than the LLM (whose own ports come from llmInputs): one typed input per name
 * in `config.promoted` that the picked model declares, like the LLM's.
 */
function appendPromotedParams(
  def: NodeDef,
  config: Record<string, unknown>,
  out: ConcretePort[],
  models?: ModelLookup,
): void {
  const present = new Set(out.map((p) => p.name));
  for (const name of nodePromoted(config)) {
    const type = promotedParamType(def, config, name, models);
    if (!type || present.has(name)) continue;
    out.push({ name, type, trigger: false, optional: true, dynamic: false, promotedParam: true });
    present.add(name);
  }
}

/**
 * Universal knob promotion: append one typed input port per name in
 * `config.promoted` that corresponds to a real WIDGET on this def (so a stale
 * promote, a name with no backing widget, mints no phantom port). The socket is
 * typed by the widget's kind (number → float, bool → bool, else text) and marked
 * `promotedParam` so the existing demote UI ("convert back to widget") applies.
 * Skipped names that already render as declared/dynamic ports are de-duped.
 */
function appendPromotedWidgets(
  def: NodeDef,
  config: Record<string, unknown>,
  out: ConcretePort[],
): void {
  const widgetByName = new Map<string, Widget>(def.widgets.map((w) => [w.name, w]));
  const present = new Set(out.map((p) => p.name));
  for (const name of nodePromoted(config)) {
    const widget = widgetByName.get(name);
    if (!widget || present.has(name)) continue;
    out.push({
      name,
      type: widgetPortType(widget.kind),
      trigger: false,
      optional: true,
      dynamic: false,
      promotedParam: true,
    });
    present.add(name);
  }
}

/**
 * The LLM's concrete input ports, derived from its model manifest + promoted set.
 * Order (top to bottom): trigger, prompt, each non-text input modality, the
 * growable `tools` base (if the model tool-calls), then each promoted param port.
 */
function llmInputs(
  def: NodeDef,
  config: Record<string, unknown>,
  connectedPortNames: ReadonlySet<string>,
  models?: ModelLookup,
): ConcretePort[] {
  const out: ConcretePort[] = [];
  const trigger = def.inputs.find((p) => p.name === "trigger");
  const prompt = def.inputs.find((p) => p.name === "prompt");
  const toolsBase = def.inputs.find((p) => p.name === "tools");

  // base inputs (always present), reading the declared port where available.
  out.push({
    name: "trigger", type: trigger?.type ?? "event",
    trigger: true, optional: trigger?.optional ?? false, dynamic: false,
  });
  out.push({
    name: "prompt", type: prompt?.type ?? "text",
    trigger: false, optional: prompt?.optional ?? false, dynamic: false,
  });

  const manifest = models?.get(llmModelId(config));

  // no model / missing manifest → just the base ports (the node still works).
  if (!manifest) {
    return out;
  }

  // one input port per non-text accepted modality (image → image, audio → audio…).
  const modalityNames: string[] = [];
  for (const m of manifest.inputs) {
    if (m === "text") continue;
    modalityNames.push(m);
    out.push({ name: m, type: m, trigger: false, optional: true, dynamic: false });
  }

  // the promoted-param names that actually exist on this model (so a stale promote
  // from a previous model is ignored rather than minting a phantom port).
  const paramByName = new Map(manifest.params.map((p) => [p.name, p]));
  const promoted = llmPromoted(config).filter((n) => paramByName.has(n));
  const promotedSet = new Set(promoted);

  // the names that are NOT part of the tools growable base: base + modality +
  // promoted. Any other connected handle materialises as a tool socket.
  const reserved = new Set<string>([
    ...LLM_BASE_INPUT_NAMES, ...modalityNames, ...promotedSet,
  ]);

  if (manifest.tools && toolsBase) {
    const mine = [...connectedPortNames]
      .filter((n) => !reserved.has(n))
      .sort(compareDynamicNames);
    for (const n of mine) {
      out.push({
        name: n, type: toolsBase.type, trigger: toolsBase.trigger,
        optional: true, dynamic: true, base: toolsBase.name,
      });
    }
    out.push({
      name: ghostHandleId(toolsBase.name), type: toolsBase.type,
      trigger: toolsBase.trigger, optional: true, dynamic: true,
      base: toolsBase.name, ghost: true,
    });
  }

  // one typed input port per promoted param (named exactly the param name).
  for (const name of promoted) {
    const param = paramByName.get(name)!;
    out.push({
      name, type: paramPortType(param.type),
      trigger: false, optional: true, dynamic: false, promotedParam: true,
    });
  }

  return out;
}

/**
 * The concrete input ports for the Build JSON node. Its single growable `field`
 * base materialises one named socket per wired key (the dst_port name is the json
 * key), in a stable order, then a ghost "add" socket. Each field input carries
 * `any` (a json object value can be anything). On a ghost drop the new key is
 * named after the source node (see FIELD_NAMED_IDS / onConnect).
 */
function buildInputs(
  def: NodeDef,
  _config: Record<string, unknown>,
  connectedPortNames: ReadonlySet<string>,
): ConcretePort[] {
  const out: ConcretePort[] = [];
  const declared = new Set(def.inputs.map((p) => p.name));
  const fieldBase = def.inputs.find((p) => p.growable);

  // any non-growable declared inputs render first (Build has none today, but keep
  // this general so a future static input on Build still appears).
  for (const p of def.inputs) {
    if (p.growable) continue;
    out.push({
      name: p.name, type: p.type, trigger: p.trigger,
      optional: p.optional, dynamic: false,
    });
  }

  if (fieldBase) {
    const mine = [...connectedPortNames]
      .filter((n) => !declared.has(n))
      .sort(compareDynamicNames);
    for (const n of mine) {
      out.push({
        name: n, type: "any", trigger: false, optional: true,
        dynamic: true, base: fieldBase.name,
      });
    }
    out.push({
      name: ghostHandleId(fieldBase.name), type: "any", trigger: false,
      optional: true, dynamic: true, base: fieldBase.name, ghost: true,
    });
  }

  appendPromotedWidgets(def, _config, out);
  return out;
}

/**
 * The concrete input ports for the HTTP Request node. The declared static
 * `trigger` renders first, then the growable `tag` base materialises one named
 * socket per wired tag (the dst_port name IS the tag name, named after the
 * source node on a ghost drop), plus a ghost "add" socket. Each tag socket
 * carries `any` so any wire type can land on it (a binary data: URL flips body
 * detection to multipart; everything else coerces to str for {tag} substitution).
 */
function httpInputs(
  def: NodeDef,
  config: Record<string, unknown>,
  connectedPortNames: ReadonlySet<string>,
): ConcretePort[] {
  const out: ConcretePort[] = [];
  const declared = new Set(def.inputs.map((p) => p.name));
  const tagBase = def.inputs.find((p) => p.growable);

  // the static declared inputs first (trigger), unchanged. The growable `tag`
  // base is declared too but materialises below, so skip it here.
  for (const p of def.inputs) {
    if (p.growable) continue;
    out.push({
      name: p.name, type: p.type, trigger: p.trigger,
      optional: p.optional, dynamic: false,
    });
  }

  if (tagBase) {
    const mine = [...connectedPortNames]
      .filter((n) => !declared.has(n))
      .sort(compareDynamicNames);
    for (const n of mine) {
      out.push({
        name: n, type: tagBase.type, trigger: false, optional: true,
        dynamic: true, base: tagBase.name,
      });
    }
    out.push({
      name: ghostHandleId(tagBase.name), type: tagBase.type,
      trigger: false, optional: true, dynamic: true,
      base: tagBase.name, ghost: true,
    });
  }

  appendPromotedWidgets(def, config, out);
  return out;
}

/** Map an HTTP `response_type` value to the wire type its `body` output carries.
 *  text/json/image/audio/video map straight through; binary and auto are `any`
 *  (the runtime fills with the actual mime at call time). */
export function httpBodyOutputType(responseType: string): string {
  switch (responseType) {
    case "text": return "text";
    case "json": return "json";
    case "image": return "image";
    case "audio": return "audio";
    case "video": return "video";
    case "binary":
    case "auto":
    default:
      return "any";
  }
}

/** A concrete OUTPUT port on a placed node (declared, or reshaped from a model). */
export interface ConcreteOutput {
  name: string;
  type: string;
}

/**
 * The concrete output ports for a placed node. For most nodes these are simply
 * the declared outputs; for the LLM they are reshaped from the selected model's
 * output modalities (text/audio/image) plus a `tool_call` port when it tool-calls,
 * the completion `trigger` and the `error` branch.
 * No model / missing manifest falls back to the declared outputs.
 */
/** Whether the LLM's thinking is switched ON (the model can think AND the think
 *  knob is not off), so the reasoning output only appears when it is actually used. */
function llmThinkingOn(config: Record<string, unknown>, manifest: ModelManifest): boolean {
  if (!manifest.thinking) return false;
  const params = (config.params as Record<string, unknown> | undefined) ?? {};
  const param = manifest.params.find((p) => p.name === "think");
  const raw = params.think !== undefined ? params.think : param?.default;
  if (typeof raw === "boolean") return raw;
  if (typeof raw === "string") {
    const s = raw.trim().toLowerCase();
    return s !== "" && s !== "off" && s !== "none" && s !== "false" && s !== "0";
  }
  return false;
}

export function concreteOutputs(
  def: NodeDef,
  config: Record<string, unknown>,
  models?: ModelLookup,
  wirelessChannels?: WirelessChannelMap,
): ConcreteOutput[] {
  // Wireless Out mirrors the sockets wired into the Wireless In on its channel,
  // so a downstream wire sees the same named (and typed) ports as the source side.
  if (def.id === WIRELESS_OUT_ID) {
    const ch = String(config.channel ?? "1");
    return (wirelessChannels?.get(ch) ?? []).map((p) => ({ name: p.name, type: p.type }));
  }
  // Split JSON reshapes its outputs from the `keys` config: one `any` port per
  // key (the reverse of Build JSON's growable inputs). Each key routes its value.
  if (def.id === SPLIT_ID) {
    return parseSplitKeys(String(config.keys ?? "")).map((key) => ({ name: key, type: "any" }));
  }
  // Tool Args fires on the `call` and drives the body: a `trigger` output plus one
  // `any` output per declared argument (the same names the tool schema is generated
  // from). Mirrors Split JSON's per-key reshape, with the leading trigger.
  if (def.id === TOOL_ARGS_ID) {
    return [
      { name: "trigger", type: "event" },
      ...parseSplitKeys(String(config.fields ?? "")).map((name) => ({ name, type: "any" })),
    ];
  }
  // HTTP reshapes its `body` output type per the `response_type` knob, so a
  // downstream Preview / wiring is type-correct (an `image` response lights up
  // image-coloured wires and renders in an image Preview).
  if (def.id === HTTP_ID) {
    const rt = String(config.response_type ?? "auto").toLowerCase();
    const bodyType = httpBodyOutputType(rt);
    return def.outputs.map((p) =>
      p.name === "body" ? { name: p.name, type: bodyType } : { name: p.name, type: p.type },
    );
  }
  // Op-shaped outputs (KV/DB, or any future node) surface only the ports whose
  // declared op_values include the live operation; ports with no op_field always
  // show. Read off the declaration, no per-id table, so a downstream Preview
  // never sees a dead port for an op that doesn't write it.
  if (def.id !== LLM_ID) {
    return def.outputs
      .filter((p) => opVisible(p, config, def.widgets))
      .map((p) => ({ name: p.name, type: p.type }));
  }
  const manifest = models?.get(llmModelId(config));
  if (!manifest) {
    return def.outputs.map((p) => ({ name: p.name, type: p.type }));
  }
  const out: ConcreteOutput[] = [];
  for (const m of manifest.outputs) {
    // the assembled reply rides the `response` port (renamed from `text`).
    if (m === "text") out.push({ name: "response", type: "text" });
    else if (m === "audio") out.push({ name: "audio", type: "audio" });
    else if (m === "image") out.push({ name: "image", type: "image" });
  }
  // a model that somehow declares no recognised output still emits a reply.
  if (out.length === 0) out.push({ name: "response", type: "text" });
  // the reasoning trace port only appears while thinking is actually switched on.
  if (llmThinkingOn(config, manifest)) out.push({ name: "reasoning", type: "text" });
  if (manifest.tools) out.push({ name: "tool_call", type: "tool-call" });
  // the completion event is declared on the node (not a model modality), so the
  // manifest reshaping must carry it through for sequencing (done -> next trigger).
  out.push({ name: "trigger", type: "event" });
  // the failure branch: fires instead of response/trigger when the provider call
  // fails. Declared on the node, so it renders and wires on every model.
  out.push({ name: "error", type: "event" });
  return out;
}

/**
 * The config a model-driven node (any node with a model widget) takes when its
 * model is switched: the new model id under the model widget's own name, params
 * reset to EXACTLY the new
 * manifest's defaults (nothing carries over, so a Fish `voice` can never reach
 * xAI as its voice_id, nor grok-4.3's `think` reach grok-4.20), and only the
 * promotions that still exist (a param of the new model, or a node widget).
 * With the manifest not loaded yet, params start empty and the backend applies
 * the model's defaults. Returns a new object; `config` is not mutated.
 */
export function modelSwitchConfig(
  def: NodeDef,
  config: Record<string, unknown>,
  modelId: string,
  manifest: ModelManifest | undefined,
): Record<string, unknown> {
  const params = Object.fromEntries((manifest?.params ?? []).map((p) => [p.name, p.default]));
  const kept = new Set([
    ...(manifest?.params ?? []).map((p) => p.name),
    ...def.widgets.map((w) => w.name),
  ]);
  const key = modelWidgetOf(def)?.name ?? "model";
  return { ...config, [key]: modelId, params, promoted: nodePromoted(config).filter((n) => kept.has(n)) };
}

/** One wire on a node that is being reshaped, seen from that node: an input
 *  wire lands on `handle` carrying `type`; an output wire leaves from `handle`. */
export type NodeWire =
  | { side: "in"; handle: string; type: string }
  | { side: "out"; handle: string };

/**
 * Whether a wire survives a reshape of its node (a model switch). An input wire
 * survives when its handle is still one of the node's concrete inputs under
 * `nextConfig` AND what it carries still fits that socket; an output wire when
 * its handle is still a concrete output. Read from concreteInputs /
 * concreteOutputs, the single source of a node's ports, so a switch can never
 * cut a wire on a port the node keeps (a TTS `text` or `lang`) nor keep one on a
 * port the new model dropped (an `image` input, a `tool_call` or `reasoning`
 * output). The type check matters for the LLM: its growable `tools` base takes
 * any unreserved handle, so a stale `image` wire would otherwise be re-read as
 * a tool socket. `compatible` is the editor's type check (lib/types
 * typesCompatible), passed in to keep this module free of runtime imports.
 */
export function survivesReshape(
  def: NodeDef,
  nextConfig: Record<string, unknown>,
  connectedInputs: ReadonlySet<string>,
  wire: NodeWire,
  models: ModelLookup | undefined,
  compatible: (outType: string, inType: string) => boolean,
): boolean {
  if (wire.side === "out") {
    return concreteOutputs(def, nextConfig, models).some((p) => p.name === wire.handle);
  }
  const port = concreteInputs(def, nextConfig, connectedInputs, models)
    .find((p) => p.name === wire.handle && !p.ghost);
  return port !== undefined && compatible(wire.type, port.type);
}

/**
 * Resolve the type of an LLM INPUT handle (for connection validation), consulting
 * the selected model's manifest. Falls back to the declared port / growable base.
 */
export function llmInputType(
  def: NodeDef,
  config: Record<string, unknown>,
  handle: string,
  models?: ModelLookup,
): string | undefined {
  const declared = def.inputs.find((p) => p.name === handle);
  if (declared && !declared.growable) return declared.type;
  const manifest = models?.get(llmModelId(config));
  if (manifest) {
    if (manifest.inputs.includes(handle) && handle !== "text") return handle; // modality port
    const param = manifest.params.find((p) => p.name === handle);
    if (param && llmPromoted(config).includes(handle)) return paramPortType(param.type);
  }
  // otherwise it is a tools growable socket.
  const toolsBase = def.inputs.find((p) => p.name === "tools");
  return toolsBase?.type;
}

/** Resolve the type of an LLM OUTPUT handle (for edge colouring + validation). */
export function llmOutputType(
  def: NodeDef,
  config: Record<string, unknown>,
  handle: string,
  models?: ModelLookup,
): string | undefined {
  const found = concreteOutputs(def, config, models).find((p) => p.name === handle);
  return found?.type;
}

/** The handle id of a growable base's ghost ("add another") socket. */
export function ghostHandleId(base: string): string {
  return `${base}·+`; // base·+ is never a valid backend dst_port
}

/** Whether a handle id is a ghost slot for any growable base. */
export function isGhostHandle(handle: string): boolean {
  return handle.endsWith("·+");
}

/** The base name a ghost handle belongs to. */
export function ghostBase(handle: string): string {
  return handle.slice(0, -2);
}

/**
 * Whether the wired socket `name` is one of the growable base `base`'s sockets,
 * decided the way the server fires and validates it (NodeSpec.growable_base).
 * Never a declared port (it is drawn as itself) nor a knob promoted to an input
 * (it is drawn as that knob's port). On a node with one growable input, every
 * other socket, whatever it is named: Compute names them `value0`, a Queue
 * after its source. On a node with several, the base its name starts with.
 */
function belongsToBase(
  name: string,
  base: string,
  def: NodeDef,
  declared: ReadonlySet<string>,
  promoted: ReadonlySet<string>,
): boolean {
  if (declared.has(name) || promoted.has(name)) return false;
  const bases = def.inputs.filter((p) => p.growable).map((p) => p.name);
  if (bases.length === 1) return bases[0] === base;
  return bases.find((b) => name.startsWith(b)) === base;
}

/** Order auto-generated dynamic names (value0, value1, …) numerically, else lexically. */
function compareDynamicNames(a: string, b: string): number {
  const na = a.match(/(\d+)$/);
  const nb = b.match(/(\d+)$/);
  if (na && nb && a.slice(0, na.index) === b.slice(0, nb.index)) {
    return Number(na[1]) - Number(nb[1]);
  }
  return a.localeCompare(b);
}

/**
 * Slugify a source node name into a valid {tag} token: lowercase, every run of
 * non-alphanumerics becomes a single underscore, edges trimmed, and a leading
 * non-letter is prefixed with `_` so the result always matches /^[a-z_]\w*$/.
 * "Chat Append (User)" -> "chat_append_user", "2nd thing" -> "_2nd_thing".
 */
export function slugifyTag(name: string): string {
  let s = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (!s) return "tag";
  if (!/^[a-z_]/.test(s)) s = `_${s}`;
  return s;
}

/**
 * Pick a fresh dynamic port name when a wire lands on a growable ghost. Template
 * tags are author-named (never auto), so this is only for generic growable bases
 * (Compute value0/value1, LLM tools tool0/tool1…). Returns a name not already
 * used on this node.
 */
export function nextDynamicName(
  base: string,
  used: ReadonlySet<string>,
): string {
  // singular stem: `values` -> `value`, `tools` -> `tool`, else the base itself.
  const stem = base.endsWith("s") ? base.slice(0, -1) : base;
  let i = 0;
  while (used.has(`${stem}${i}`)) i += 1;
  return `${stem}${i}`;
}
