// ============================================================================
// paletteGroups: the headings the ⌘K palette lists its rows under, in the order
// it draws them. The arrow keys walk one flat list of rows, so that list is put
// in this order before any row gets its index: ↓ always lands on the row drawn
// below, whatever order a caller passes its actions in.
// ============================================================================

export const PALETTE_GROUPS = ["Add node", "Actions", "Help", "Go to node"] as const;
export type PaletteGroup = (typeof PALETTE_GROUPS)[number];

/** `rows` in PALETTE_GROUPS order, each group keeping the order it came in. */
export function inGroupOrder<T extends { group: PaletteGroup }>(rows: T[]): T[] {
  return PALETTE_GROUPS.flatMap((group) => rows.filter((row) => row.group === group));
}
