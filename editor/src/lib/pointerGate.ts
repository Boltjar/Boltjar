// ============================================================================
// pointerGate: tells a real pointer move from a synthetic one. When a list
// reorders under a pointer that is not moving (typing in a search box, the
// list scrolling its active row into view), Chrome sends the row now under the
// pointer mouseenter and mouseover, and may send a mousemove at the same
// coordinates. A list that picks its active row on hover must ignore all of
// those, or the row under a resting pointer takes the highlight away from the
// best match and Enter runs the wrong row.
// ============================================================================

export interface PointerGate {
  /** Record a mousemove at (x, y) in client pixels. True only when the pointer
   *  really moved since the last one this gate saw: the first move after the
   *  gate is made, and a move at the same point, are not moves. */
  moved(x: number, y: number): boolean;
}

export function pointerGate(): PointerGate {
  let last: { x: number; y: number } | null = null;
  return {
    moved(x, y) {
      const was = last;
      last = { x, y };
      return was !== null && (was.x !== x || was.y !== y);
    },
  };
}
