// ============================================================================
// Node-kind language: each kind maps to a header colour token, an identity
// Ionicon, and the UPPERCASE tag shown in the header + inspector. The 9
// backend kinds collapse onto the 6 design colours.
// ============================================================================
import type { NodeKind, NodeDef } from "../types/protocol";

export interface KindStyle {
  /** CSS custom property for the kind colour. */
  colorVar: string;
  token: string;
  /** Default identity glyph (Ionicon name). */
  icon: string;
  /** UPPERCASE machine tag for the header / inspector. */
  tag: string;
  /** Library group bucket this kind belongs to. */
  group: string;
}

const KIND: Record<NodeKind, KindStyle> = {
  trigger: { colorVar: "var(--kind-trigger)", token: "--kind-trigger", icon: "flash", tag: "SOURCE", group: "Triggers" },
  sensor: { colorVar: "var(--kind-service)", token: "--kind-service", icon: "time-outline", tag: "SERVICE", group: "Services" },
  value: { colorVar: "var(--kind-transform)", token: "--kind-transform", icon: "code-slash-outline", tag: "VALUE", group: "Values" },
  transform: { colorVar: "var(--kind-transform)", token: "--kind-transform", icon: "git-compare-outline", tag: "TRANSFORM", group: "Transforms" },
  logic: { colorVar: "var(--kind-logic)", token: "--kind-logic", icon: "git-branch-outline", tag: "LOGIC", group: "Logic" },
  service: { colorVar: "var(--kind-service)", token: "--kind-service", icon: "server-outline", tag: "SERVICE", group: "Services" },
  store: { colorVar: "var(--kind-service)", token: "--kind-service", icon: "albums-outline", tag: "STORE", group: "Services" },
  output: { colorVar: "var(--kind-output)", token: "--kind-output", icon: "exit-outline", tag: "OUTPUT", group: "Outputs" },
  subgraph: { colorVar: "var(--kind-subgraph)", token: "--kind-subgraph", icon: "cube-outline", tag: "SUBGRAPH", group: "Subgraphs" },
};

export function kindStyle(kind: NodeKind): KindStyle {
  return KIND[kind] ?? KIND.transform;
}

// Per-node-id identity glyphs. Falls back to the kind glyph.
// Keyed to the core pack ids (boltjar/nodes/core/builtin.py).
const NODE_ICON: Record<string, string> = {
  // triggers
  "core.trigger.interval": "timer-outline",
  "core.trigger.manual": "play-circle-outline",
  "core.trigger.chat": "chatbubble-ellipses-outline",
  // sensors
  "core.sensor.clock": "time-outline",
  // values
  "core.value.text": "text-outline",
  "core.value.integer": "calculator-outline",
  "core.value.float": "calculator-outline",
  "core.value.boolean": "toggle-outline",
  // data / transforms
  "core.data.template": "document-text-outline",
  "core.data.compute": "calculator-outline",
  "core.logic.condition": "git-branch-outline",
  // ai
  "core.ai.llm": "sparkles-outline",
  "core.ai.stt": "mic-outline",
  "core.ai.tts": "volume-high-outline",
  // store
  "core.store.memory": "albums-outline",
  "core.store.state": "save-outline",
  // files
  "core.file.read": "document-text-outline",
  "core.file.write": "create-outline",
  "core.file.append": "add-circle-outline",
  "core.file.delete": "trash-outline",
  "core.file.list": "folder-open-outline",
  // services
  "core.service.http": "globe-outline",
  // outputs / inspect
  "core.output.log": "terminal-outline",
  "core.output.preview": "eye-outline",
  "core.output.chat": "chatbubbles-outline",
  "core.output.deliver": "paper-plane-outline",
};

/** The identity glyph for a node definition: a per-id override or its kind glyph. */
export function nodeIcon(def: NodeDef): string {
  return NODE_ICON[def.id] ?? kindStyle(def.kind).icon;
}

// Ordering of library groups, top to bottom, in the palette.
export const GROUP_ORDER = [
  "Triggers",
  "Values",
  "Transforms",
  "Logic",
  "AI",
  "Services",
  "Files",
  "Outputs",
  "Inspect",
  "Subgraphs",
];

/**
 * Which library group a definition belongs to. We honour the backend's
 * `category` when it is a known group, otherwise bucket by kind. The core
 * pack uses categories Values / Triggers / Sensors / Data / Logic / AI / Store /
 * Output / Inspect, which we normalise onto the palette's groups.
 */
export function libraryGroup(def: NodeDef): string {
  const byCategory: Record<string, string> = {
    Values: "Values",
    Triggers: "Triggers",
    Sensors: "Services",
    Data: "Transforms",
    Transforms: "Transforms",
    AI: "AI",
    Logic: "Logic",
    Service: "Services",
    Services: "Services",
    Store: "Services",
    Files: "Files",
    Output: "Outputs",
    Outputs: "Outputs",
    Inspect: "Inspect",
    Subgraphs: "Subgraphs",
  };
  return byCategory[def.category] ?? kindStyle(def.kind).group;
}

// ----------------------------------------------------------------------------
// Colour-by-FUNCTION. Colour tracks what a node DOES (its library bucket:
// Sources / Values / Transforms / Logic / AI / Services / Outputs / Inspect /
// Subgraphs), not its coarse backend kind. So the AI nodes (kind=transform)
// read purple, the Inspect taps (kind=output) read grey, and Value / Transform
// / AI stay visibly distinct. One --fn-* token per function (src/styles/tokens.css).
// ----------------------------------------------------------------------------
const GROUP_COLOR: Record<string, string> = {
  Triggers: "var(--fn-source)",
  Values: "var(--fn-value)",
  Transforms: "var(--fn-transform)",
  Logic: "var(--fn-logic)",
  AI: "var(--fn-ai)",
  Services: "var(--fn-service)",
  Files: "var(--fn-files)",
  Outputs: "var(--fn-output)",
  Inspect: "var(--fn-inspect)",
  Subgraphs: "var(--fn-subgraph)",
};

/** The function colour for a node, keyed off its library group. Used for the
 *  node header, library icon, inspector accent and minimap dot. */
export function functionColorVar(def: NodeDef): string {
  return GROUP_COLOR[libraryGroup(def)] ?? "var(--ink-dim)";
}

/** The function colour for a named library group (its header dot). */
export function groupColorVar(group: string): string {
  return GROUP_COLOR[group] ?? "var(--ink-dim)";
}
