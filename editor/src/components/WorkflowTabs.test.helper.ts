// ============================================================================
// WorkflowTabs.test.helper: pure functions used by the WorkflowTabs component
// for overflow + reorder logic. Kept side-effect-free + framework-free so they
// are trivially eyeball-testable in isolation and easy to unit-test later.
//
// Three primitives are exported:
//   - computeVisible(order, active, maxVisible) -> { visible, overflow }
//     The strip shows the first (maxVisible - 1) slots PLUS a single "More N"
//     button in the last slot when overflow exists. The active tab is always
//     forced into the visible group (see the swap-in rule).
//   - reorder(arr, fromIndex, toIndex) -> new array
//     Moves an item; toIndex is the index AFTER removal.
//   - bringToFront(order, slug) -> new array
//     Moves slug to position 0. Used when activating from the overflow panel.
// ============================================================================

export interface VisibilitySplit {
  /** Tabs rendered as normal tabs in the strip (length <= maxVisible - 1, or
   *  <= maxVisible when there is no overflow). */
  visible: string[];
  /** Tabs hidden behind the "More N" dropdown. Empty when total <= maxVisible. */
  overflow: string[];
}

/**
 * Split `order` into the visible-strip + overflowed-dropdown groups.
 *
 * Algorithm:
 *  - If `order.length <= maxVisible`: everything fits as normal tabs (no More).
 *  - Otherwise: visible = order.slice(0, maxVisible - 1) and overflow = the
 *    rest. We reserve the last visible slot for the "More N" button, so only
 *    (maxVisible - 1) actual tabs render in the strip.
 *  - Active-stays-visible rule: if `active` is non-null and would land in
 *    overflow, swap-in: drop the LAST visible tab into overflow's front and
 *    insert active at the end of visible. This keeps the visible cap intact
 *    while guaranteeing active is on screen.
 */
export function computeVisible(
  order: string[],
  active: string | null,
  maxVisible: number,
): VisibilitySplit {
  if (maxVisible <= 0) return { visible: [], overflow: order.slice() };
  if (order.length <= maxVisible) {
    return { visible: order.slice(), overflow: [] };
  }
  const cap = maxVisible - 1; // reserve last slot for "More N"
  const visible = order.slice(0, cap);
  const overflow = order.slice(cap);
  if (active && !visible.includes(active) && overflow.includes(active)) {
    // displace the rightmost visible tab so active comes on screen.
    const displaced = visible[visible.length - 1];
    visible[visible.length - 1] = active;
    // remove active from overflow, push displaced to overflow's front.
    const nextOverflow = overflow.filter((s) => s !== active);
    nextOverflow.unshift(displaced);
    return { visible, overflow: nextOverflow };
  }
  return { visible, overflow };
}

/**
 * Move `arr[fromIndex]` to `toIndex`. `toIndex` is the index in the array AFTER
 * the source has been removed (so reorder(['a','b','c'], 0, 2) => ['b','c','a']).
 * Returns a NEW array; the input is not mutated.
 */
export function reorder<T>(arr: T[], fromIndex: number, toIndex: number): T[] {
  if (fromIndex < 0 || fromIndex >= arr.length) return arr.slice();
  const next = arr.slice();
  const [item] = next.splice(fromIndex, 1);
  const clamped = Math.max(0, Math.min(toIndex, next.length));
  next.splice(clamped, 0, item);
  return next;
}

/**
 * Move `slug` to index 0 of `order` (no-op if absent or already first).
 * Used when the user activates a tab from the More dropdown so the activated
 * tab is always inside the visible group.
 */
export function bringToFront(order: string[], slug: string): string[] {
  const idx = order.indexOf(slug);
  if (idx <= 0) return order.slice();
  const next = order.slice();
  const [item] = next.splice(idx, 1);
  next.unshift(item);
  return next;
}

/**
 * Validate that `next` is a permutation of `current` (same length + same
 * members). Used by App.reorderTabs to refuse malformed updates.
 */
export function isPermutation(current: string[], next: string[]): boolean {
  if (current.length !== next.length) return false;
  const a = current.slice().sort();
  const b = next.slice().sort();
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
  return true;
}
