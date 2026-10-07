// ============================================================================
// wireRoute: the shape of a wire that goes BACK to the left (its target socket
// sits left of its source socket: a loop closed into an earlier node, like
// For-each's `loop` or a Tool's `result`). A bezier between two such sockets
// cuts straight back through the bodies of its own two nodes, so a back wire
// is routed around them instead: it leaves its source to the right, turns down
// (or up, whichever way is shorter) past both nodes with a margin, runs back
// left under (or over) them, and enters its target from the left. The corners
// are rounded, so it reads as one deliberate line.
//
// Pure geometry, no React: TypedEdge feeds it the two sockets and the two
// endpoint nodes' boxes (flow coordinates).
// ============================================================================

export interface Point { x: number; y: number }
/** A node's box in flow coordinates. */
export interface Box { x: number; y: number; width: number; height: number }

/** How far a back wire keeps from its nodes, and how round its corners are. */
export const BACK_WIRE = { margin: 28, radius: 14 } as const;

/** Is a wire from `s` to `t` a back wire (its target left of its source)? */
export function isBackWire(s: Point, t: Point): boolean {
  return t.x < s.x;
}

/** The corner points of a back wire from socket `s` (on node box `sb`) to
 *  socket `t` (on node box `tb`): source, four corners, target. The run sits
 *  `margin` below the lower node's bottom or above the upper node's top,
 *  whichever makes the shorter wire (below on a tie); the two upright legs sit
 *  `margin` right of the source's node and left of the target's, and step out
 *  further when the other endpoint node stands in a leg's way. */
export function backWirePoints(s: Point, t: Point, sb: Box, tb: Box, margin: number = BACK_WIRE.margin): Point[] {
  const below = Math.max(sb.y + sb.height, tb.y + tb.height) + margin;
  const above = Math.min(sb.y, tb.y) - margin;
  const costBelow = (below - s.y) + (below - t.y);
  const costAbove = (s.y - above) + (t.y - above);
  const runY = costAbove < costBelow ? above : below;

  // a leg is the upright segment between a socket's height and the run; it must
  // not cross the OTHER endpoint node either (a wide node, a target lower down).
  const legHits = (x: number, y0: number, y1: number, b: Box) =>
    x > b.x - margin && x < b.x + b.width + margin &&
    Math.max(y0, y1) > b.y && Math.min(y0, y1) < b.y + b.height;

  let right = Math.max(s.x, sb.x + sb.width) + margin;
  if (legHits(right, s.y, runY, tb)) right = Math.max(right, tb.x + tb.width + margin);
  let left = Math.min(t.x, tb.x) - margin;
  if (legHits(left, t.y, runY, sb)) left = Math.min(left, sb.x - margin);

  return [
    { x: s.x, y: s.y },
    { x: right, y: s.y },
    { x: right, y: runY },
    { x: left, y: runY },
    { x: left, y: t.y },
    { x: t.x, y: t.y },
  ];
}

const dist = (a: Point, b: Point) => Math.hypot(b.x - a.x, b.y - a.y);
const fmt = (n: number) => String(Math.round(n * 100) / 100);

/** An SVG path through `pts` with each inner corner rounded to `radius` (less
 *  where a segment is too short to hold it: an inner segment shares its length
 *  between the two corners on its ends, an end segment gives its whole length
 *  to its one corner). */
export function roundedPath(pts: Point[], radius: number = BACK_WIRE.radius): string {
  if (pts.length < 2) return "";
  let d = `M${fmt(pts[0].x)},${fmt(pts[0].y)}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const prev = pts[i - 1], c = pts[i], next = pts[i + 1];
    const lin = dist(prev, c), lout = dist(c, next);
    const r = Math.min(radius, i === 1 ? lin : lin / 2, i === pts.length - 2 ? lout : lout / 2);
    if (r <= 0 || lin === 0 || lout === 0) {
      d += ` L${fmt(c.x)},${fmt(c.y)}`;
      continue;
    }
    const a = { x: c.x + ((prev.x - c.x) / lin) * r, y: c.y + ((prev.y - c.y) / lin) * r };
    const b = { x: c.x + ((next.x - c.x) / lout) * r, y: c.y + ((next.y - c.y) / lout) * r };
    d += ` L${fmt(a.x)},${fmt(a.y)} Q${fmt(c.x)},${fmt(c.y)} ${fmt(b.x)},${fmt(b.y)}`;
  }
  const last = pts[pts.length - 1];
  return d + ` L${fmt(last.x)},${fmt(last.y)}`;
}

/** The drawn path of a back wire, or null when the wire goes forward (it keeps
 *  its bezier) or a node's box is not known yet. */
export function backWirePath(s: Point, t: Point, sb: Box | null, tb: Box | null): string | null {
  if (!isBackWire(s, t) || !sb || !tb) return null;
  return roundedPath(backWirePoints(s, t, sb, tb));
}
