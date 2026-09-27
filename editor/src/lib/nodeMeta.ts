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
 * The mono subline under a node's title: the most identifying config, e.g.
 * `interval · 60s`, `grok · grok-4`, `template · 2 tags`. Falls back to the
 * node's category in lowercase.
 */
export function headerSubline(def: NodeDef, config: Record<string, unknown>): string {
  // a model node (declared by its model widget, any pack's too): the model it
  // runs, the picked one, else the widget's declared default.
  const modelWidget = modelWidgetOf(def);
  if (modelWidget) {
    return modelSubline(String(config[modelWidget.name] || modelWidget.default || ""));
  }
  switch (def.id) {
    case "core.trigger.interval": {
      const s = config.seconds ?? defaultOf(def, "seconds") ?? 2;
      return `every · ${s}s`;
    }
    case "core.trigger.manual":
      return "manual · once";
    case "core.trigger.chat":
      return "chat · on send";
    case "core.sensor.clock":
      return "clock · volatile";
    case "core.value.text":
      return `text · ${preview(config.text ?? "", 16) || "empty"}`;
    case "core.value.integer":
      return `int · ${config.number ?? 0}`;
    case "core.value.float":
      return `float · ${config.number ?? 0}`;
    case "core.value.boolean":
      return `bool · ${config.on ? "true" : "false"}`;
    case "core.data.template": {
      const tpl = String(config.template ?? defaultOf(def, "template") ?? "");
      const tags = templateTags(def, tpl).length;
      return `template · ${tags} tag${tags === 1 ? "" : "s"}`;
    }
    case "core.data.compute":
      return `compute · ${preview(config.expression ?? "value", 14)}`;
    case "core.logic.condition":
      return `route · ${preview(config.expression ?? "value", 14)}`;
    case "core.store.memory":
      return "memory · recall";
    case "core.store.state":
      return "state · held";
    case "core.output.log":
      return `log · ${preview(config.label ?? "log", 14)}`;
    case "core.output.preview":
      return "preview · live tap";
    case "core.output.deliver":
      return `deliver · ${preview(config.channel ?? "default", 12)}`;
    default:
      return def.category.toLowerCase();
  }
}

/** A model id as a subline: `xai/tts` -> `xai · tts`, a bare id -> `model · id`. */
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
  // Prefer the widgets that carry the node's identity.
  const priority: Record<string, string[]> = {
    "core.data.template": ["template"],
    "core.data.compute": ["expression"],
    "core.logic.condition": ["expression"],
    "core.value.text": ["text"],
    "core.store.state": ["initial"],
  };
  const names = priority[def.id] ?? [];
  for (const name of names) {
    const w = def.widgets.find((x) => x.name === name);
    if (!w) continue;
    const raw = name in config ? config[name] : w.default;
    const val = preview(raw, 28);
    if (val !== "") rows.push({ key: w.label || name, value: val });
  }
  return rows.slice(0, 2);
}
