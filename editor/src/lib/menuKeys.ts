// ============================================================================
// menuKeys: where a key moves focus inside a ContextMenu (the WAI-ARIA menu
// pattern). Focus walks the enabled items top to bottom and wraps. A menu with
// a node search keeps its search box as one more stop after the last item, so
// ↓ from the last item and ↑ from the first land in it, and ↑ from the top of
// its results comes back up to the last item.
// ============================================================================

/**
 * The stop `key` moves focus to from stop `from`. Stops 0 to items - 1 are the
 * enabled items; with `search`, stop `items` is the search box. null when the
 * key does not move focus (or there is no item to move to).
 */
export function menuFocusTarget(key: string, from: number, items: number, search: boolean): number | null {
  const stops = items + (search ? 1 : 0);
  if (items === 0 || from < 0 || from >= stops) return null;
  switch (key) {
    case "ArrowDown":
      return (from + 1) % stops;
    case "ArrowUp":
      return (from - 1 + stops) % stops;
    case "Home":
      return 0;
    case "End":
      return items - 1;
    default:
      return null;
  }
}
