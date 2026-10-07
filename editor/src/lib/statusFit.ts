// ============================================================================
// statusFit: how the status bar's latest line fits beside the counters. The
// counters, coordinates and buttons on the right keep their room; the line on
// the left shrinks first: its message ellipsizes down to a readable minimum,
// and when its answer buttons still do not fit beside that, they collapse
// into one "N choices" button that opens the console, where the whole line
// and its buttons are. Nothing in the bar ever overlaps.
// ============================================================================

/** Do a line's buttons fit at full size? `tail` is the room the line has,
 *  `fixed` what its time and level icon take, `msgMin` the message's readable
 *  minimum, `offer` the buttons' full width and `gap` the space between items. */
export function offerFits(tail: number, fixed: number, msgMin: number, offer: number, gap: number): boolean {
  return fixed + gap + msgMin + gap + offer <= tail;
}

/** The label of the collapsed button: how many answers wait in the console. */
export function choicesLabel(n: number): string {
  return `${n} choice${n === 1 ? "" : "s"}`;
}
