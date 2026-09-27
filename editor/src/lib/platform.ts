// ============================================================================
// platform: tiny OS check so keyboard hints read in the user's own language.
// macOS shows the ⌘ glyph (no separator: "⌘S"); every other OS shows the
// spelled modifier ("Ctrl S"). Used by the chrome's hover tooltips and the
// command palette / context-menu shortcut chips.
// ============================================================================

/** True on macOS, where the Command key is the primary modifier. */
export const IS_MAC =
  typeof navigator !== "undefined" &&
  /mac/i.test(navigator.platform || navigator.userAgent || "");

/** The primary modifier label for this OS: "⌘" on macOS, "Ctrl" elsewhere. */
export const MOD = IS_MAC ? "⌘" : "Ctrl";

/**
 * Format a modifier chord for display, e.g. mod("S") -> "⌘S" (mac) / "Ctrl S"
 * (Windows/Linux). Non-modifier keys (Del, ↵) should be passed through as-is by
 * the caller, not through this helper.
 */
export function mod(key: string): string {
  return IS_MAC ? `${MOD}${key}` : `${MOD} ${key}`;
}
