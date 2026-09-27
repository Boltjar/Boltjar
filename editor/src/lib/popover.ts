// ============================================================================
// popover: where a screen-space popover (the model picker's list) sits against
// the button that opened it. Below the button when its whole height fits there,
// above it when only that side has room, and otherwise on the roomier side with
// its height capped to that room (the list scrolls). It always touches the
// button across `gap`, measured by the popover's real height, so a short list
// never floats away from its button, whatever the canvas zoom. A button panned
// out of view still gets a popover inside the viewport.
//
// While it stays open the popover keeps the side it opened on (`side`), so
// typing in its search box never flips it across the button, and an upward
// popover keeps the tallest height it has had (`heldHeight`), so its search
// row, at the top, stays put while the results narrow. Pure, so the node tests
// drive it.
// ============================================================================

export interface PopoverRequest {
  /** the button, in viewport pixels. */
  trigger: { left: number; top: number; bottom: number; width: number };
  /** the popover's natural height: its content, capped by its own max height. */
  height: number;
  /** the popover's least width; it is never narrower than the button. */
  minWidth: number;
  /** the least height worth opening on one side; with less room on both
   *  sides the popover takes the viewport's height and may cover the button. */
  minHeight: number;
  viewport: { width: number; height: number };
  /** the space between the button and the popover. */
  gap: number;
  /** the least space kept between the popover and the viewport's edges. */
  margin: number;
  /** the side it opened on, kept while it stays open. It is left only when the
   *  button has moved (a pan) until that side has less than `minHeight`. */
  side?: "down" | "up";
}

export interface PopoverPlacement {
  left: number;
  top: number;
  width: number;
  /** the height the popover may take (its natural height when that fits). */
  maxHeight: number;
  side: "down" | "up";
}

export function placePopover(r: PopoverRequest): PopoverPlacement {
  const { trigger, viewport, gap, margin } = r;
  const width = Math.min(Math.max(trigger.width, r.minWidth), viewport.width - 2 * margin);
  const left = clamp(trigger.left, margin, viewport.width - margin - width);

  const below = viewport.height - margin - (trigger.bottom + gap);
  const above = trigger.top - gap - margin;
  let side: "down" | "up";
  let height: number;
  const kept = r.side === "down" ? below : r.side === "up" ? above : -1;
  if (r.side && kept >= Math.min(r.height, r.minHeight)) {
    side = r.side;
    height = Math.min(r.height, kept);
  } else if (r.height <= below) {
    side = "down";
    height = r.height;
  } else if (r.height <= above) {
    side = "up";
    height = r.height;
  } else {
    side = below >= above ? "down" : "up";
    const room = Math.max(below, above);
    height = room >= Math.min(r.height, r.minHeight)
      ? room
      : Math.min(r.height, viewport.height - 2 * margin);
  }
  height = Math.max(0, height);

  const against = side === "down" ? trigger.bottom + gap : trigger.top - gap - height;
  const top = clamp(against, margin, viewport.height - margin - height);
  return { left, top, width, maxHeight: height, side };
}

/** The height an open popover lays out at: its natural height, except that an
 *  upward one never shrinks while open (`held` is the tallest it has been). Its
 *  bottom sits on the button, so shrinking would move its top, and the search
 *  row there, while the list narrows. A downward one hangs from the button and
 *  shrinks from the bottom, so it keeps its natural height. */
export function heldHeight(side: "down" | "up" | undefined, natural: number, held: number): number {
  return side === "up" ? Math.max(natural, held) : natural;
}

/** `value` held inside [low, high]; `low` wins when the range is empty. */
function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(value, high));
}
