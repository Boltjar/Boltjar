// ============================================================================
// panHint: where the "drag to pan · scroll to zoom" pill sits on the canvas's
// bottom row. The row also holds the React Flow credit (bottom-left) and the
// zoom cluster with the minimap (bottom-right), so the pill takes the free
// stretch between them: centred on the canvas when it fits there, slid toward
// the credit as far as it must when the canvas narrows, and hidden once even
// that stretch is narrower than the pill. Pure, so the node tests drive it.
// ============================================================================

export interface PanHintRow {
  /** the canvas width. */
  width: number;
  /** the pill's own width. */
  hint: number;
  /** where the credit ends, from the canvas's left edge (0 when there is none). */
  left: number;
  /** where the zoom cluster starts, from the canvas's left edge. */
  right: number;
  /** the space kept between the pill and each neighbour. */
  gap: number;
  /** the canvas's edge inset, the least room kept on the left. */
  inset: number;
}

/** The pill's centre, from the canvas's left edge, or null when it has no room. */
export function panHintCenter({ width, hint, left, right, gap, inset }: PanHintRow): number | null {
  const from = Math.max(left + gap, inset);
  const to = right - gap;
  if (hint <= 0 || to - from < hint) return null;
  const half = hint / 2;
  return Math.min(Math.max(width / 2, from + half), to - half);
}
