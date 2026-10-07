// ============================================================================
// The value a select knob stores. The shared Select is string-valued (it shows
// and emits text), but a declaration may carry TYPED options: a model param such
// as xAI's sample_rate lists ints (8000, 16000, ..., 44100). Storing the chosen
// text would save "44100" where the manifest declares 44100, so the knob hands
// back the declared option itself. Pure, so it is testable without React.
// ============================================================================

/** The declared option the chosen `text` stands for: a primitive option keeps its
 *  own type (44100 stays a number), a {value,label} option yields its value, and
 *  a text that matches no option passes through unchanged. */
export function declaredOption(options: readonly unknown[], text: string): unknown {
  for (const option of options) {
    const value = option && typeof option === "object" ? (option as { value: unknown }).value : option;
    if (String(value) === text) return value;
  }
  return text;
}

/** The on/off state a bool knob shows. A graph saved while the knob was still a
 *  text box can hold "false" or "0"; those read as off, never as a truthy
 *  string. Only "true", "1", "yes" and "on" read as on (the runtime's
 *  Widget.coerce reads a saved value the same way). An unset value shows the
 *  declared default. */
export function knobBool(value: unknown, fallback?: unknown): boolean {
  const v = value ?? fallback;
  if (typeof v === "string") return ["true", "1", "yes", "on"].includes(v.trim().toLowerCase());
  return Boolean(v);
}

/** The Convert to input action a knob offers: `onPromote`, or none when its
 *  widget declares promotable=false. Every knob path (Knob, a code field, a
 *  secret-aware text, a store dropdown) reads the flag through this one call. */
export function convertAction<T>(promotable: boolean | undefined, onPromote: T): T | undefined {
  return promotable === false ? undefined : onPromote;
}

/** The width a knob's value asks for on its row, in characters of the mono
 *  face: its own length, never less than `min` (room to type into an empty
 *  field). A label and a value that do not fit together wrap the value under
 *  the label, so a knob never hides its own value. */
export function valueChars(value: unknown, min = 4): number {
  return Math.max(min, String(value ?? "").length);
}
