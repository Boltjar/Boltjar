// ============================================================================
// The type system: one colour per data type, plus connection compatibility.
// The design system owns the canonical palette, so we map every type name to
// its --t-* token (not the seed hex the backend ships). Ports, chips, wires and
// the legend all read from here: add a type in one place.
// ============================================================================

/** Maps a data-type name to its CSS custom property (the --t-* token). */
const TYPE_TOKEN: Record<string, string> = {
  event: "--t-event",
  text: "--t-text",
  number: "--t-float", // number has no dedicated token; share the float cyan
  int: "--t-int",
  float: "--t-float",
  bool: "--t-bool",
  json: "--t-json",
  list: "--t-json",
  image: "--t-image",
  audio: "--t-audio",
  "pcm-audio": "--t-pcm-audio",
  embedding: "--t-embedding",
  "memory-set": "--t-memory-set",
  state: "--t-state",
  message: "--t-message",
  tool: "--t-tool",
  "tool-call": "--t-tool-call",
  "tool-result": "--t-tool-result",
  mood: "--t-mood",
  action: "--t-action",
  schedule: "--t-schedule",
  observation: "--t-observation",
  secret: "--t-secret",
  db: "--t-db",
  kv: "--t-kv",
  any: "--t-any",
};

/**
 * Resolve a type name to a `var(--t-*)` reference. Unknown types fall back to
 * the untyped wildcard graphite so an unrecognised pipe still reads as a pipe.
 */
export function typeColorVar(type: string | undefined): string {
  const token = (type && TYPE_TOKEN[type]) || "--t-any";
  return `var(${token})`;
}

/** The raw token name (without `var()`), for places that compose their own ref. */
export function typeToken(type: string | undefined): string {
  return (type && TYPE_TOKEN[type]) || "--t-any";
}

// Numeric family: int/float are sub-types of number and interchange with it.
const NUMERIC = new Set(["number", "int", "float"]);

/**
 * Single-parent subtyping, mirroring the `parent` column of boltjar.sdk._Types
 * (_CORE_TYPES). An output of a subtype may feed an input of its supertype. This
 * is directional: `tool-call` (a Tool node's `call` output) is accepted by a
 * `tool` input (the LLM's growable `tools` port, the wire that offers a Tool to
 * the model), while a bare `tool` does not satisfy a `tool-call` input. Keep this
 * in sync with the backend registry (the numeric family is handled separately
 * above as a bidirectional set).
 */
const SUBTYPE_OF: Record<string, string> = {
  "tool-call": "tool",
  lang: "text",
  vectors: "db",
  // a list IS json-shaped: List.out (`list`) feeds any `json` input (For-each's
  // `list`, DB rows, Format List). Directional: a bare json is not a list.
  list: "json",
};

/**
 * Whether an output of `outType` may connect to an input of `inType`.
 *
 * Mirrors boltjar.sdk._Types.compatible:
 *   - `any` on either side is compatible with everything,
 *   - identical types are compatible,
 *   - int/float/number interchange (the numeric family),
 *   - a subtype output satisfies its supertype input (SUBTYPE_OF).
 */
export function typesCompatible(
  outType: string | undefined,
  inType: string | undefined,
): boolean {
  if (!outType || !inType) return true;
  if (outType === "any" || inType === "any") return true;
  if (outType === inType) return true;
  if (NUMERIC.has(outType) && NUMERIC.has(inType)) return true;
  // walk the subtype chain: an output may feed any ancestor-typed input.
  for (let t: string | undefined = outType; t; t = SUBTYPE_OF[t]) {
    if (t === inType) return true;
  }
  return false;
}
