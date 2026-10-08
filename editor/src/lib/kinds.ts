// ============================================================================
// Node-kind language: each kind maps to a header colour token, an identity
// Ionicon (drawn by a node that declares no icon of its own), and the
// UPPERCASE tag shown in the header + inspector. The 9 backend kinds collapse
// onto the 6 design colours.
// ============================================================================
import type { NodeKind, NodeDef } from "../types/protocol";

export interface KindStyle {
  /** CSS custom property for the kind colour. */
  colorVar: string;
  token: string;
  /** The identity glyph (Ionicon name) of a node that declares none. */
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

/** The identity glyph for a node definition: the icon its @node declares, else
 *  its kind glyph. `known` says whether the editor ships an icon by that name
 *  (lib/icons `hasIcon`), so a custom node naming one it does not ship draws the kind
 *  glyph rather than the neutral dot an unknown name renders as. */
export function nodeIcon(def: NodeDef, known: (name: string) => boolean): string {
  const declared = def.icon ?? "";
  return declared && known(declared) ? declared : kindStyle(def.kind).icon;
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
 * nodes use categories Values / Triggers / Sensors / Data / Logic / AI / Store /
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
