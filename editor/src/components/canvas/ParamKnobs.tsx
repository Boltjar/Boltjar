// ============================================================================
// ParamKnobs: a model node's per-model parameter knobs (LLM, TTS, STT, Rerank,
// any node with a model picker). These render through the
// single shared Knob component, so a model param looks and behaves exactly like
// any other node's knob (value + slider for bounded numbers, toggle, dropdown,
// text; convert-to-input and reset-to-default on the knob's right-click menu).
// One knob per manifest param that is NOT promoted to an input port.
// ============================================================================
import type { ModelManifest, ModelParam } from "../../types/protocol";
import { Knob, type KnobKind } from "./Knob";

interface ParamKnobsProps {
  manifest: ModelManifest;
  /** the node's config.params (name -> value). */
  params: Record<string, unknown>;
  /** the promoted param names (these render as ports, not knobs, so skipped here). */
  promoted: string[];
  variant?: "node" | "inspector";
  onParamChange: (name: string, value: unknown) => void;
  onPromote: (name: string) => void;
}

/** Map a model param's type onto the shared Knob kind. */
function knobKind(type: ModelParam["type"]): KnobKind {
  switch (type) {
    case "float":
    case "int": return "number";
    case "bool": return "bool";
    case "select": return "select";
    case "text":
    default: return "text";
  }
}

export function ParamKnobs({
  manifest,
  params,
  promoted,
  variant = "node",
  onParamChange,
  onPromote,
}: ParamKnobsProps) {
  const promotedSet = new Set(promoted);
  const knobs = manifest.params.filter((p) => !promotedSet.has(p.name));
  if (knobs.length === 0) return null;

  return (
    <div className={`knobs ${variant}`}>
      {knobs.map((p) => (
        <Knob
          key={p.name}
          label={p.label || p.name}
          kind={knobKind(p.type)}
          value={p.name in params ? params[p.name] : p.default}
          default={p.default}
          min={p.min}
          max={p.max}
          step={p.step ?? (p.type === "int" ? 1 : 0.01)}
          options={p.options}
          onChange={(v) => onParamChange(p.name, v)}
          onConvert={() => onPromote(p.name)}
          onReset={() => onParamChange(p.name, p.default)}
        />
      ))}
    </div>
  );
}
