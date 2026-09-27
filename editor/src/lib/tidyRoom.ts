// ============================================================================
// tidyRoom: how much height Tidy up keeps free below a node that grows later,
// so it does not cover the node below it when it does. Read from the node's
// declaration, never its id:
//   • a node with a model picker (a widget of kind "model") and no model picked
//     is short; picking one adds that model's knobs (and maybe port rows). It
//     keeps room for the largest model of its family (the manifests of that
//     family). One with a model picked is already its real size: no room.
//   • a node with a growable input gains a socket row per new {tag} or wire; a
//     modest allowance of GROW_ROWS rows covers the next few.
// The pixel sizes are the editor's own, measured on 2026-09-27 in the running
// editor at 100% (styles/editor.css .knob, .knob-slider, .llm-nomodel,
// --port-pitch): e.g. an LLM on qwen3:14b is 314 px taller than with no model,
// which is what modelRoom gives for its 7 params. Pure (type-only imports).
// ============================================================================
import type { ModelManifest, ModelParam, NodeDef } from "../types/protocol";

export const TIDY_ROOM = {
  /** one knob's height by what it draws. */
  knob: { slider: 53, number: 42, select: 41, text: 31, bool: 30 },
  /** the gap between knobs (.knobs gap) and the knob list's border. */
  knobGap: 6,
  knobBorder: 2,
  /** the "pick a model" line the knobs replace. */
  noModel: 34,
  /** the model picker is this much taller with a model picked. */
  picked: 7,
  /** one port row (--port-pitch). */
  portPitch: 24,
  /** socket rows kept free on a node with a growable input. */
  growRows: 2,
} as const;

function knobHeight(p: Pick<ModelParam, "type" | "min" | "max">): number {
  const k = TIDY_ROOM.knob;
  switch (p.type) {
    case "float":
    case "int":
      return p.min !== null && p.min !== undefined && p.max !== null && p.max !== undefined ? k.slider : k.number;
    case "bool": return k.bool;
    case "select": return k.select;
    default: return k.text;
  }
}

/** The height a model node with no model gains at most when one of `manifests`
 *  is picked: that model's knobs in place of the "pick a model" line, plus the
 *  port rows it adds (`rowsWith(m)` against `rowsNow`). Never below 0. */
export function modelRoom<M extends Pick<ModelManifest, "params">>(
  manifests: M[],
  rowsWith: (m: M) => number,
  rowsNow: number,
): number {
  let most = 0;
  for (const m of manifests) {
    const params = m.params ?? [];
    const knobs = params.length
      ? params.reduce((s, p) => s + knobHeight(p), 0) + TIDY_ROOM.knobGap * (params.length - 1) + TIDY_ROOM.knobBorder
      : 0;
    const rows = Math.max(0, rowsWith(m) - rowsNow) * TIDY_ROOM.portPitch;
    most = Math.max(most, knobs - TIDY_ROOM.noModel + TIDY_ROOM.picked + rows);
  }
  return Math.max(0, most);
}

/**
 * The room Tidy up keeps below a node of `def` with `config`. `manifests` is
 * the live model list; `portRows(config)` is how many port rows the node draws
 * under that config (the larger of its concrete inputs and outputs).
 */
export function tidyRoom(
  def: Pick<NodeDef, "widgets" | "inputs">,
  config: Record<string, unknown>,
  manifests: Array<Pick<ModelManifest, "id" | "kind" | "params">>,
  portRows: (config: Record<string, unknown>) => number,
): number {
  const picker = def.widgets.find((w) => w.kind === "model");
  if (picker) {
    const picked = config[picker.name];
    if (typeof picked === "string" && picked !== "") return 0;
    const family = picker.model_kind || "llm";
    const own = manifests.filter((m) => (m.kind ?? "llm") === family);
    return modelRoom(
      own,
      (m) => portRows({ ...config, [picker.name]: m.id }),
      portRows(config),
    );
  }
  if (def.inputs.some((p) => p.growable)) return TIDY_ROOM.growRows * TIDY_ROOM.portPitch;
  return 0;
}
