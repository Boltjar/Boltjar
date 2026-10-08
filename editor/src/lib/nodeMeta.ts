// ============================================================================
// Derived, human-facing summaries of a node: the header subline, the library
// capability hint, and a compact body summary of its config. Pure functions of
// (NodeDef, config) so they stay testable and the components stay dumb.
// ============================================================================
import type { NodeDef, Port } from "../types/protocol";
import { modelWidgetOf, templateTags } from "./dynamicPorts";

/** Truncate a value to a short, single-line preview for dense surfaces. */
function preview(value: unknown, max = 22): string {
  if (value === null || value === undefined) return "";
  let s = typeof value === "string" ? value : JSON.stringify(value);
  s = s.replace(/\s+/g, " ").trim();
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

/**
 * The mono subline under a node's title: the `subline` its @node declares,
 * rendered against its config (renderSubline), e.g. `every · 60s`,
 * `xai · grok-4.20`, `template · 2 tags`. A model node that declares none
 * shows its model; any other shows its category in lowercase.
 */
export function headerSubline(def: NodeDef, config: Record<string, unknown>): string {
  if (def.subline) return renderSubline(def, config);
  // a model node that declares no subline (any custom node's): the model picked in
  // it. A model picker never holds a model nobody picked (a model can cost
  // money), so an empty one reads as none picked.
  const modelWidget = modelWidgetOf(def);
  if (modelWidget) return modelSubline(String(config[modelWidget.name] || ""));
  return def.category.toLowerCase();
}

/** A subline placeholder: `{field}` or `{field|filter|filter:arg}` (the same
 *  grammar boltjar/sdk.py checks when a node is declared). */
const PLACEHOLDER = /\{([A-Za-z_][A-Za-z0-9_]*)((?:\|[a-z]+(?::[^|}]*)?)*)\}/g;

/**
 * Fill a subline template. Each placeholder reads its field from the config,
 * or the field's declared default when the config does not set it, then passes
 * it through its filters in order (SUBLINE_FILTERS in boltjar/sdk.py):
 * `clip:N` one line cut to N chars, `or:TEXT` TEXT when empty, `bool`
 * true/false, `model` a model id as `provider · model`, `tags` the {tags} a
 * template holds as `N tags`. Text outside the placeholders is kept as is.
 */
export function renderSubline(def: NodeDef, config: Record<string, unknown>): string {
  return (def.subline ?? "").replace(PLACEHOLDER, (_match, name: string, filters: string) => {
    const fallback = defaultOf(def, name);
    let value: unknown = config[name] ?? fallback;
    for (const f of filters.split("|").slice(1)) {
      const colon = f.indexOf(":");
      const filter = colon < 0 ? f : f.slice(0, colon);
      const arg = colon < 0 ? undefined : f.slice(colon + 1);
      value = applyFilter(def, filter, arg, value);
    }
    return value === null || value === undefined ? "" : String(value);
  });
}

function applyFilter(def: NodeDef, filter: string, arg: string | undefined, value: unknown): unknown {
  switch (filter) {
    case "clip":
      return preview(value, Number(arg) || 22);
    case "or":
      return value === null || value === undefined || String(value) === "" ? arg ?? "" : value;
    case "bool":
      return value ? "true" : "false";
    case "model":
      // an empty pick reads as none picked: no node picks a model on its own
      return modelSubline(String(value || ""));
    case "tags": {
      const n = templateTags(def, String(value ?? "")).length;
      return `${n} tag${n === 1 ? "" : "s"}`;
    }
    default:
      return value;
  }
}

/** The fields a node's subline names, in order: the config that identifies it. */
export function sublineFields(def: NodeDef): string[] {
  return [...(def.subline ?? "").matchAll(PLACEHOLDER)].map((m) => m[1]);
}

/** A model id as a subline: `xai/tts` -> `xai · tts`, a bare id -> `model · id`,
 *  nothing picked -> `model · none picked`, the one wording every model node
 *  (LLM, TTS, STT, Embed, Rerank, a custom node's) shows with no model picked. */
export function modelSubline(id: string): string {
  if (!id) return "model · none picked";
  // split at the FIRST slash only: an endpoint model id keeps its own slashes
  // (openrouter/meta-llama/llama-3.3-70b-instruct).
  const cut = id.indexOf("/");
  return cut > 0 ? `${id.slice(0, cut)} · ${id.slice(cut + 1)}` : `model · ${id}`;
}

/** The default value of a widget by name, if declared. */
export function defaultOf(def: NodeDef, name: string): unknown {
  return def.widgets.find((w) => w.name === name)?.default;
}

/** A compact `in,in → out` capability hint for a library row (mono micro). */
export function capabilityHint(def: NodeDef): string {
  const ins = uniqueTypes(def.inputs);
  const outs = uniqueTypes(def.outputs);
  const left = ins.length ? ins.join(",") : "∅";
  const right = outs.length ? outs.join(",") : "∅";
  return `${left} → ${right}`;
}

function uniqueTypes(ports: Port[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const p of ports) {
    if (!seen.has(p.type)) {
      seen.add(p.type);
      out.push(p.type);
    }
  }
  return out.slice(0, 3);
}

/** A single key/value summary row for the node body, or null if none fits. */
export interface BodySummary {
  key: string;
  value: string;
}

/**
 * The most informative config row(s) to surface in the node body. Kept to one
 * or two rows so the card stays dense.
 */
export function bodySummary(def: NodeDef, config: Record<string, unknown>): BodySummary[] {
  const rows: BodySummary[] = [];
  // the node's identity text: the code fields its subline names (a Text's text,
  // a Compute's expression), read from the declaration, never a per-id list.
  for (const name of sublineFields(def)) {
    const w = def.widgets.find((x) => x.name === name);
    if (!w || w.kind !== "code" || rows.some((r) => r.key === (w.label || name))) continue;
    const raw = name in config ? config[name] : w.default;
    const val = preview(raw, 28);
    if (val !== "") rows.push({ key: w.label || name, value: val });
  }
  return rows.slice(0, 2);
}
