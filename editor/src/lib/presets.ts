// ============================================================================
// Node presets: tiny, DATA-DEFINED subgraph templates the picker drops as one
// unit (nodes + edges, pre-wired). A preset is NOT a special-cased node: it is a
// {label, nodes, edges} record rendered from this list and materialised by
// useGraph.addPreset, so adding a preset = adding an entry here (no new UI, no
// per-id branch). The picker renders each with the SAME library-item markup.
// ============================================================================

/** One node in a preset, offset (dx,dy in flow units) from the drop point. */
export interface PresetNode {
  typeId: string;
  dx: number;
  dy: number;
  /** config overrides layered on top of the node's widget defaults (optional). */
  config?: Record<string, unknown>;
}

/** One wire in a preset, addressing nodes by their index in `nodes`. */
export interface PresetEdge {
  from: [number, string]; // [node index, output port name]
  to: [number, string]; // [node index, input port name]
}

/** A picker preset: a labelled, pre-wired cluster of nodes. */
export interface NodePreset {
  id: string;
  label: string;
  /** the mono capability hint shown under the label (matches library items). */
  cap: string;
  /** an Ionicon key (see lib/icons.tsx REGISTRY). */
  icon: string;
  nodes: PresetNode[];
  edges: PresetEdge[];
}

export const PRESETS: NodePreset[] = [
  {
    id: "preset.tool_with_body",
    label: "Tool (with body)",
    cap: "tool + args, pre-wired",
    icon: "construct-outline",
    nodes: [
      { typeId: "core.ai.tool", dx: 0, dy: 0 },
      { typeId: "core.ai.tool_args", dx: 300, dy: 0 },
    ],
    // Tool.call (tool-call) -> Tool Args.call: the body entry the runtime keys on
    // (the backend's _tool_arg_fields reads edges_from (tool,"call")).
    edges: [{ from: [0, "call"], to: [1, "call"] }],
  },
];
