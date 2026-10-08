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
// Several back wires between the same two nodes (an LLM's trigger and its
// response going back into one node) take parallel lanes LANE_SPACING apart on
// every segment, nested so they never cross (pairLanes).
//
// Pure geometry, no React: TypedEdge feeds it the two sockets, the two
// endpoint nodes' boxes (flow coordinates) and the pair's other back wires.
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

export type Side = "below" | "above";

/** Which side of its two nodes a back wire runs on: `margin` below the lower
 *  node's bottom or above the upper node's top, whichever makes the shorter
 *  wire (below on a tie). */
export function backWireSide(s: Point, t: Point, sb: Box, tb: Box, margin: number = BACK_WIRE.margin): Side {
  const below = Math.max(sb.y + sb.height, tb.y + tb.height) + margin;
  const above = Math.min(sb.y, tb.y) - margin;
  const costBelow = (below - s.y) + (below - t.y);
  const costAbove = (s.y - above) + (t.y - above);
  return costAbove < costBelow ? "above" : "below";
}

/** A back wire's place among the back wires between the same two nodes, so
 *  parallel wires never draw over each other. Lane 0 hugs the nodes, each next
 *  lane sits `spacing` further out. `src` sets the right leg and the run, `tgt`
 *  the left leg, and all of them share one `side`. */
export interface Lane { side: Side; src: number; tgt: number; spacing: number }

/** Gap between two parallel back wires (CSS px in flow units). */
export const LANE_SPACING = 9;

/** One back wire of a node pair: its edge id and its two sockets' heights. */
export interface PairWire { id: string; sy: number; ty: number }

/** The lanes of the back wires between one pair of nodes. They share a side
 *  (chosen from their middle socket heights), and nest so they never cross:
 *  the wire whose source socket is nearest the run takes the inner lane of the
 *  right leg and the run, the wire whose target socket is nearest the run the
 *  inner lane of the left leg. When the two orders agree, no two wires cross;
 *  when they disagree, a crossing is forced by the sockets themselves, and it
 *  happens once. Ties fall back to the edge id, so every wire gets one place. */
export function pairLanes(
  wires: readonly PairWire[], sx: number, tx: number, sb: Box, tb: Box,
  spacing: number = LANE_SPACING, margin: number = BACK_WIRE.margin,
): Map<string, Lane> {
  const lanes = new Map<string, Lane>();
  if (wires.length === 0) return lanes;
  const mid = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const side = backWireSide({ x: sx, y: mid(wires.map((w) => w.sy)) }, { x: tx, y: mid(wires.map((w) => w.ty)) }, sb, tb, margin);
  // nearest the run first: below, the lowest socket; above, the highest
  const near = (a: number, b: number) => (side === "below" ? b - a : a - b);
  const byId = (a: PairWire, b: PairWire) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const src = [...wires].sort((a, b) => near(a.sy, b.sy) || byId(a, b));
  const tgt = [...wires].sort((a, b) => near(a.ty, b.ty) || byId(a, b));
  for (const w of wires) {
    lanes.set(w.id, { side, src: src.indexOf(w), tgt: tgt.indexOf(w), spacing });
  }
  return lanes;
}

/** The corner points of a back wire from socket `s` (on node box `sb`) to
 *  socket `t` (on node box `tb`): source, four corners, target. The run sits
 *  `margin` beyond the nodes on its side (backWireSide, or the lane's side);
 *  the two upright legs sit `margin` right of the source's node and left of the
 *  target's, and step out further when the other endpoint node stands in a
 *  leg's way. A lane moves each segment out by its own lane number. */
export function backWirePoints(
  s: Point, t: Point, sb: Box, tb: Box, margin: number = BACK_WIRE.margin, lane?: Lane,
): Point[] {
  const side = lane?.side ?? backWireSide(s, t, sb, tb, margin);
  const out = (n: number) => n * (lane?.spacing ?? 0);
  const runY = side === "above"
    ? Math.min(sb.y, tb.y) - margin - out(lane?.src ?? 0)
    : Math.max(sb.y + sb.height, tb.y + tb.height) + margin + out(lane?.src ?? 0);

  // a leg is the upright segment between a socket's height and the run; it must
  // not cross the OTHER endpoint node either (a wide node, a target lower down).
  const legHits = (x: number, y0: number, y1: number, b: Box) =>
    x > b.x - margin && x < b.x + b.width + margin &&
    Math.max(y0, y1) > b.y && Math.min(y0, y1) < b.y + b.height;

  let right = Math.max(s.x, sb.x + sb.width) + margin;
  if (legHits(right, s.y, runY, tb)) right = Math.max(right, tb.x + tb.width + margin);
  right += out(lane?.src ?? 0);
  let left = Math.min(t.x, tb.x) - margin;
  if (legHits(left, t.y, runY, sb)) left = Math.min(left, sb.x - margin);
  left -= out(lane?.tgt ?? 0);

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

/** The curvature TypedEdge hands React Flow's getBezierPath. Over the default
 *  0.25 it keeps a wire leaving its port perpendicular. */
export const WIRE_CURVATURE = 0.35;

/** The four points of the bezier React Flow's getBezierPath draws from a right
 *  hand socket `s` to a left hand socket `t` (@xyflow/system: a control sits
 *  half the gap out when the target is ahead, curvature * 25 * sqrt(gap) when
 *  it is behind). */
export function forwardWireControls(s: Point, t: Point, curvature: number = WIRE_CURVATURE): Point[] {
  const offset = (d: number) => (d >= 0 ? 0.5 * d : curvature * 25 * Math.sqrt(-d));
  return [
    { x: s.x, y: s.y },
    { x: s.x + offset(t.x - s.x), y: s.y },
    { x: t.x - offset(t.x - s.x), y: t.y },
    { x: t.x, y: t.y },
  ];
}

/** Points along a wire as the editor draws it, at most `step` apart: the
 *  bezier for a forward wire, the route around its two nodes for a back wire
 *  (corners left square, which only widens it). Used to keep wires clear of
 *  the nodes they do not connect (lib/tidyLayout). */
export function wireSamples(s: Point, t: Point, sb: Box, tb: Box, lane?: Lane, step = 4): Point[] {
  const out: Point[] = [];
  if (isBackWire(s, t)) {
    const pts = backWirePoints(s, t, sb, tb, BACK_WIRE.margin, lane);
    out.push(pts[0]);
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i];
      const n = Math.max(1, Math.ceil(dist(a, b) / step));
      for (let k = 1; k <= n; k++) out.push({ x: a.x + ((b.x - a.x) * k) / n, y: a.y + ((b.y - a.y) * k) / n });
    }
    return out;
  }
  const [p0, p1, p2, p3] = forwardWireControls(s, t);
  const reach = dist(p0, p1) + dist(p1, p2) + dist(p2, p3);
  const n = Math.max(2, Math.ceil(reach / step));
  for (let k = 0; k <= n; k++) {
    const u = k / n, v = 1 - u;
    const a = v * v * v, b = 3 * v * v * u, c = 3 * v * u * u, d = u * u * u;
    out.push({ x: a * p0.x + b * p1.x + c * p2.x + d * p3.x, y: a * p0.y + b * p1.y + c * p2.y + d * p3.y });
  }
  return out;
}

/** The drawn path of a back wire, or null when the wire goes forward (it keeps
 *  its bezier) or a node's box is not known yet. */
export function backWirePath(s: Point, t: Point, sb: Box | null, tb: Box | null, lane?: Lane): string | null {
  if (!isBackWire(s, t) || !sb || !tb) return null;
  return roundedPath(backWirePoints(s, t, sb, tb, BACK_WIRE.margin, lane));
}
