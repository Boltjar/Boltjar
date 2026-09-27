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
