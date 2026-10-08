// ============================================================================
// tidyLayout: the Tidy up command's layout. Nodes go in columns left to right
// following the wires, with even gaps and nothing touching:
//   • a node's column is the longest path to it from the graph's sources (a
//     loop, e.g. a Tool's `result`, For-each's `loop` or a Queue's `ack`, is
//     broken at the wire that comes back to where it starts, so it never
//     hangs); a source that feeds only later columns sits just before its
//     first consumer;
//   • the order inside a column cuts wire crossings (barycenter sweeps over
//     the wires, long wires counted through every column they pass);
//   • columns are as wide as their widest node; nodes in a column stack with a
//     fixed gap and sit level (top to top) with the nodes they are wired to;
//   • unconnected parts are laid out on their own and stacked top to bottom in
//     the order they had; a node group's members stay in one part and next to
//     each other, with room for the group's box;
//   • wires behind nodes they do not connect are cleared where it is cheap:
//     every drawn wire is traced the way the editor draws it (the bezier
//     forward, the route around its two nodes back, lib/wireRoute) from its
//     measured sockets; the plain layout and one where each wire that skips
//     columns holds a lane through them (a band the column's nodes keep clear
//     of) are both cleared by small slides that keep every column's order
//     (clearWires), neither growing past MAX_GROWTH of the plain layout's
//     height, and the better one is kept; a wire that cannot be cleared within
//     that is left;
//   • every position lands on the editor's grid.
// Sizes are the real measured ones; `room` keeps space free below a node that
// grows later. Deterministic: the same graph gives the same layout. Pure (its
// one import is the wire geometry), so the node tests drive it.
// ============================================================================

import { pairLanes, wireSamples, type Lane, type Point } from "./wireRoute";

/** A socket's centre relative to its node's top-left, by port name. */
export type SocketOffsets = Record<string, Point>;
/** A node's input and output sockets. */
export interface TidySockets { in?: SocketOffsets; out?: SocketOffsets }

export interface TidyNode {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  /** height kept free below the node for when it grows (lib/tidyRoom). */
  room?: number;
  /** the node group whose box wraps this node, if any. */
  group?: string | null;
  /** where its sockets sit (measured), so wires are traced from them; a
   *  socket not listed is taken at mid height of the header row. */
  sockets?: TidySockets;
}

export interface TidyEdge {
  source: string;
  target: string;
  /** how many columns the target sits right of the source: 1 (the default),
   *  or 0 for a link that may share a column (a Wireless In and its Out). */
  span?: number;
  /** the ports the wire joins (React Flow's sourceHandle / targetHandle). */
  sourceHandle?: string | null;
  targetHandle?: string | null;
  /** false for a link the editor does not draw (a Wireless In to its Out). */
  drawn?: boolean;
}

export interface TidyOptions {
  /** between the widest node of a column and the next column. */
  columnGap: number;
  /** between two nodes of a column. */
  rowGap: number;
  /** between two unconnected parts stacked top to bottom. */
  laneGap: number;
  /** the editor's snap grid. */
  grid: number;
  /** how far a group's box reaches past its members (GroupsLayer groupRect). */
  groupPad: { side: number; top: number; bottom: number };
}

export const TIDY_DEFAULTS: TidyOptions = {
  columnGap: 96,
  rowGap: 48,
  laneGap: 96,
  grid: 12,
  groupPad: { side: 22, top: 52, bottom: 22 },
};

export type Positions = Record<string, { x: number; y: number }>;

type Opts = TidyOptions;

function options(partial?: Partial<TidyOptions>): Opts {
  return {
    ...TIDY_DEFAULTS,
    ...partial,
    groupPad: { ...TIDY_DEFAULTS.groupPad, ...(partial?.groupPad ?? {}) },
  };
}

const snap = (v: number, g: number) => Math.round(v / g) * g;
const snapUp = (v: number, g: number) => Math.ceil(v / g - 1e-9) * g;
const cmp = (a: number, b: number) => (a < b ? -1 : a > b ? 1 : 0);
const cmpId = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
const tall = (n: TidyNode) => n.height + Math.max(0, n.room ?? 0);

/** Left to right, then top to bottom, then by id: the order a graph is read in. */
function readingOrder(a: TidyNode, b: TidyNode): number {
  return cmp(a.x, b.x) || cmp(a.y, b.y) || cmpId(a.id, b.id);
}

/**
 * New positions for `nodes`, laid out as a whole and anchored at their current
 * top-left (snapped to the grid). Wires to nodes outside `nodes` are ignored.
 */
export function tidyLayout(nodes: TidyNode[], edges: TidyEdge[], partial?: Partial<TidyOptions>): Positions {
  const o = options(partial);
  if (nodes.length === 0) return {};
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const ranked = [...nodes].sort(readingOrder);
  const rank = new Map(ranked.map((n, i) => [n.id, i]));
  const byRank = (a: string, b: string) => rank.get(a)! - rank.get(b)!;

  // one wire per ordered pair inside the scope, never a node to itself
  const succ = new Map<string, string[]>(nodes.map((n) => [n.id, []]));
  const pred = new Map<string, string[]>(nodes.map((n) => [n.id, []]));
  const span = new Map<string, number>();
  for (const e of edges) {
    if (e.source === e.target || !byId.has(e.source) || !byId.has(e.target)) continue;
    const key = `${e.source}\u0000${e.target}`;
    const len = e.span === 0 ? 0 : 1;
    if (span.has(key)) {
      span.set(key, Math.max(span.get(key)!, len));
      continue;
    }
    span.set(key, len);
    succ.get(e.source)!.push(e.target);
    pred.get(e.target)!.push(e.source);
  }
  for (const list of succ.values()) list.sort(byRank);
  for (const list of pred.values()) list.sort(byRank);

  // parts: wires join nodes, and so does membership of one group
  const parent = new Map(nodes.map((n) => [n.id, n.id]));
  const find = (id: string): string => {
    let r = id;
    while (parent.get(r) !== r) r = parent.get(r)!;
    let c = id;
    while (parent.get(c) !== r) {
      const next = parent.get(c)!;
      parent.set(c, r);
      c = next;
    }
    return r;
  };
  const join = (a: string, b: string) => {
    const ra = find(a);
    const rb = find(b);
    if (ra === rb) return;
    // the earlier node in reading order names the part
    if (rank.get(ra)! < rank.get(rb)!) parent.set(rb, ra);
    else parent.set(ra, rb);
  };
  for (const [s, list] of succ) for (const t of list) join(s, t);
  const firstOfGroup = new Map<string, string>();
  for (const n of ranked) {
    if (!n.group) continue;
    const first = firstOfGroup.get(n.group);
    if (first) join(first, n.id);
    else firstOfGroup.set(n.group, n.id);
  }
  const parts = new Map<string, string[]>();
  for (const n of ranked) {
    const r = find(n.id);
    if (!parts.has(r)) parts.set(r, []);
    parts.get(r)!.push(n.id);
  }

  // each part laid out on its own, then stacked in the order the parts had
  const laid = [...parts.values()].map((ids) => {
    const top = Math.min(...ids.map((id) => byId.get(id)!.y));
    const left = Math.min(...ids.map((id) => byId.get(id)!.x));
    const inPart = new Set(ids);
    const wires = edges.filter((e) => e.drawn !== false && e.source !== e.target && inPart.has(e.source) && inPart.has(e.target));
    return { ids, top, left, ...layoutPart(ids, byId, succ, pred, span, rank, wires, o) };
  });
  laid.sort((a, b) => cmp(a.top, b.top) || cmp(a.left, b.left) || byRank(a.ids[0], b.ids[0]));

  const out: Positions = {};
  let cursor = 0;
  for (const part of laid) {
    const dy = cursor - part.extent.top;
    for (const [id, p] of Object.entries(part.pos)) out[id] = { x: p.x, y: p.y + dy };
    cursor = snapUp(dy + part.extent.bottom + o.laneGap, o.grid);
  }

  // anchored at the scope's current top-left
  const ax = snap(Math.min(...nodes.map((n) => n.x)), o.grid);
  const ay = snap(Math.min(...nodes.map((n) => n.y)), o.grid);
  const minX = Math.min(...Object.values(out).map((p) => p.x));
  const minY = Math.min(...Object.values(out).map((p) => p.y));
  for (const p of Object.values(out)) {
    p.x += ax - minX;
    p.y += ay - minY;
  }
  return out;
}

interface PartLayout {
  pos: Positions;
  /** the part's vertical reach, group boxes included, relative to `pos`. */
  extent: { top: number; bottom: number };
}

function layoutPart(
  ids: string[],
  byId: Map<string, TidyNode>,
  succ: Map<string, string[]>,
  pred: Map<string, string[]>,
  span: Map<string, number>,
  rank: Map<string, number>,
  wires: TidyEdge[],
  o: Opts,
): PartLayout {
  const len = (a: string, b: string) => span.get(`${a}\u0000${b}`) ?? 1;
  const str = (v?: string | null) => v ?? "";
  wires = [...wires].sort((a, b) => rank.get(a.source)! - rank.get(b.source)! || rank.get(a.target)! - rank.get(b.target)!
    || cmpId(str(a.sourceHandle), str(b.sourceHandle)) || cmpId(str(a.targetHandle), str(b.targetHandle)));
  const wireDrop = new Map<string, number>();
  for (const e of wires) {
    const at = socketAt(byId.get(e.source)!, { x: 0, y: 0 }, "out", e.sourceHandle).y;
    const key = `${e.source}\u0000${e.target}`;
    wireDrop.set(key, Math.max(wireDrop.get(key) ?? -Infinity, at));
  }
  // ── 1. break loops. The nodes are put in one sequence (Eades, Lin and
  //       Smyth's greedy order): sinks go last, sources first, and inside a
  //       loop the node that sends the most more than it receives goes first,
  //       ties in reading order. A wire pointing back in that sequence closes a
  //       loop and is left out of the layering: a Tool's `result`, a For-each's
  //       `loop` and a Queue's `ack` all come back into the node that starts
  //       the loop, which sends more than it receives.
  const left = new Set(ids);
  const degree = (id: string, map: Map<string, string[]>) => map.get(id)!.filter((w) => left.has(w)).length;
  const head: string[] = [];
  const tail: string[] = [];
  const firstOf = (pick: (id: string) => boolean) => ids.find((id) => left.has(id) && pick(id));
  while (left.size) {
    let v: string | undefined;
    if ((v = firstOf((id) => degree(id, succ) === 0))) {
      tail.unshift(v);
    } else if ((v = firstOf((id) => degree(id, pred) === 0))) {
      head.push(v);
    } else {
      let best = -Infinity;
      for (const id of ids) {
        if (!left.has(id)) continue;
        const d = degree(id, succ) - degree(id, pred);
        if (d > best) {
          best = d;
          v = id;
        }
      }
      head.push(v!);
    }
    left.delete(v!);
  }
  const place = new Map([...head, ...tail].map((id, i) => [id, i]));
  const dagSucc = new Map<string, string[]>(ids.map((id) => [id, []]));
  const dagPred = new Map<string, string[]>(ids.map((id) => [id, []]));
  for (const s of ids) {
    for (const t of succ.get(s)!) {
      if (place.get(t)! < place.get(s)!) continue; // back wire: closes a loop
      dagSucc.get(s)!.push(t);
      dagPred.get(t)!.push(s);
    }
  }

  // ── 2. columns: the longest path from a source
  const layer = new Map<string, number>(ids.map((id) => [id, 0]));
  const indeg = new Map(ids.map((id) => [id, dagPred.get(id)!.length]));
  const queue = ids.filter((id) => indeg.get(id) === 0);
  for (let qi = 0; qi < queue.length; qi++) {
    const v = queue[qi];
    for (const w of dagSucc.get(v)!) {
      layer.set(w, Math.max(layer.get(w)!, layer.get(v)! + len(v, w)));
      indeg.set(w, indeg.get(w)! - 1);
      if (indeg.get(w) === 0) queue.push(w);
    }
  }
  // a source that feeds only later columns moves up to the column just before
  // its first consumer, so its wire stays short (a Time read by the last
  // template sits beside it, not at the far left under everything)
  for (const id of ids) {
    const outs = dagSucc.get(id)!;
    if (dagPred.get(id)!.length || !outs.length) continue;
    layer.set(id, Math.max(0, Math.min(...outs.map((w) => layer.get(w)! - len(id, w)))));
  }
  const depth = Math.max(...ids.map((id) => layer.get(id)!)) + 1;

  // ── 3. order inside the columns. A wire that skips columns is followed
  //       through each one it crosses by a sizeless stand-in, so it counts.
  const centerY = new Map<string, number>(ids.map((id) => {
    const n = byId.get(id)!;
    return [id, n.y + n.height / 2];
  }));
  const up = new Map<string, string[]>();
  const down = new Map<string, string[]>();
  const link = (a: string, b: string) => {
    if (!down.has(a)) down.set(a, []);
    if (!up.has(b)) up.set(b, []);
    down.get(a)!.push(b);
    up.get(b)!.push(a);
  };
  const cols: string[][] = Array.from({ length: depth }, () => []);
  for (const id of ids) cols[layer.get(id)!].push(id);
  const standIn = new Map<string, number>(); // stand-in id -> initial y
  const laneAt = new Map<string, number>(); // stand-in id -> its wire's height below a top
  let k = 0;
  for (const s of ids) {
    for (const t of dagSucc.get(s)!) {
      const from = layer.get(s)!;
      const to = layer.get(t)!;
      if (to === from) continue; // a link inside one column
      let prev = s;
      for (let l = from + 1; l < to; l++) {
        const d = `\u0000${k++}`;
        const f = (l - from) / (to - from);
        standIn.set(d, centerY.get(s)! + (centerY.get(t)! - centerY.get(s)!) * f);
        laneAt.set(d, wireDrop.get(`${s}\u0000${t}`) ?? 0);
        cols[l].push(d);
        link(prev, d);
        prev = d;
      }
      link(prev, t);
    }
  }
  const startY = (id: string) => (standIn.has(id) ? standIn.get(id)! : centerY.get(id)!);
  const tieRank = (id: string) => (standIn.has(id) ? ids.length : rank.get(id)!);
  for (const col of cols) {
    col.sort((a, b) => cmp(startY(a), startY(b)) || cmp(tieRank(a), tieRank(b)) || cmpId(a, b));
  }

  const crossings = (layers: string[][]): number => {
    let total = 0;
    for (let i = 0; i + 1 < layers.length; i++) {
      const at = new Map(layers[i + 1].map((id, j) => [id, j]));
      const segs: Array<[number, number]> = [];
      layers[i].forEach((id, j) => {
        for (const w of down.get(id) ?? []) segs.push([j, at.get(w)!]);
      });
      for (let a = 0; a < segs.length; a++) {
        for (let b = a + 1; b < segs.length; b++) {
          const [a1, a2] = segs[a];
          const [b1, b2] = segs[b];
          if ((a1 < b1 && a2 > b2) || (a1 > b1 && a2 < b2)) total++;
        }
      }
    }
    return total;
  };

  const reorder = (col: string[], nbr: Map<string, string[]>, other: string[]) => {
    const at = new Map(other.map((id, j) => [id, j]));
    const scale = col.length > 1 && other.length > 1 ? (other.length - 1) / (col.length - 1) : 1;
    const key = new Map<string, number>();
    col.forEach((id, j) => {
      const ns = (nbr.get(id) ?? []).filter((w) => at.has(w));
      key.set(id, ns.length ? ns.reduce((s, w) => s + at.get(w)!, 0) / ns.length : j * scale);
    });
    const was = new Map(col.map((id, j) => [id, j]));
    col.sort((a, b) => cmp(key.get(a)!, key.get(b)!) || cmp(was.get(a)!, was.get(b)!));
  };

  let best = cols.map((c) => [...c]);
  let bestCross = crossings(cols);
  for (let it = 0; it < 12 && bestCross > 0; it++) {
    if (it % 2 === 0) for (let i = 1; i < depth; i++) reorder(cols[i], up, cols[i - 1]);
    else for (let i = depth - 2; i >= 0; i--) reorder(cols[i], down, cols[i + 1]);
    const c = crossings(cols);
    if (c < bestCross) {
      bestCross = c;
      best = cols.map((col) => [...col]);
    }
  }

  // a group's members sit next to each other where the group first appears;
  // the stand-ins keep their places between them
  const column: string[][] = best.map((col) => {
    const real = col.filter((id) => !standIn.has(id));
    const first = new Map<string, number>();
    real.forEach((id, j) => {
      const g = byId.get(id)!.group;
      if (g && !first.has(g)) first.set(g, j);
    });
    const was = new Map(real.map((id, j) => [id, j]));
    const at = (id: string) => {
      const g = byId.get(id)!.group;
      return g ? first.get(g)! : was.get(id)!;
    };
    const sorted = [...real].sort((a, b) => cmp(at(a), at(b)) || cmp(was.get(a)!, was.get(b)!));
    let r = 0;
    return col.map((id) => (standIn.has(id) ? id : sorted[r++]));
  });

  // ── 4. x: each column as wide as its widest node
  const colX: number[] = [];
  let x = 0;
  for (const col of column) {
    colX.push(x);
    const width = Math.max(0, ...col.filter((id) => !standIn.has(id)).map((id) => byId.get(id)!.width));
    x = snapUp(x + width + o.columnGap, o.grid);
  }

  // ── 5. y: stack each column with its gaps, then sit each node level with
  //       the nodes it is wired to, keeping the order and the gaps. A stand-in
  //       is the lane its wire takes through the column: a band around the
  //       height the wire leaves its source at, which the column's nodes keep
  //       clear of, so a wire that skips columns runs between their nodes.
  const groupOf = (id: string) => (standIn.has(id) ? null : byId.get(id)!.group ?? null);
  const gapAfter = (a: string, b: string) => {
    const ga = groupOf(a);
    const gb = groupOf(b);
    if (ga === gb) return o.rowGap;
    return o.rowGap + (ga ? o.groupPad.bottom : 0) + (gb ? o.groupPad.top : 0);
  };
  // what an item takes up below its top: a node all of its height, a lane a
  // band at its wire's height
  const reach = (id: string): [number, number] => {
    if (!standIn.has(id)) return [0, tall(byId.get(id)!)];
    const h = laneAt.get(id)!;
    return [h - LANE_BAND, h + LANE_BAND];
  };
  // two nodes keep the row gap (and a group's pad) even with lanes between
  // them: each side of a lane holds half of it, two lanes sit LANE_GAP apart
  const sep = (a: string, b: string) => {
    const la = standIn.has(a), lb = standIn.has(b);
    let gap = gapAfter(a, b);
    if (la && lb) gap = LANE_GAP;
    else if (la) gap = o.rowGap / 2 + (groupOf(b) ? o.groupPad.top : 0);
    else if (lb) gap = o.rowGap / 2 + (groupOf(a) ? o.groupPad.bottom : 0);
    return reach(a)[1] - reach(b)[0] + gap;
  };
  const beside = (id: string, map: Map<string, string[]>) =>
    standIn.has(id) ? [] : map.get(id)!.filter((w) => layer.get(w) === layer.get(id));
  // with lanes, the columns hold their stand-ins and a node's neighbours are
  // read through them, so a long wire is pulled level end to end; without,
  // the plain layered layout: nodes only, levelled with the nodes they are
  // wired to
  const stack = (lanes: boolean): Positions => {
    const cols = lanes ? column : column.map((col) => col.filter((id) => !standIn.has(id)));
    const before = lanes
      ? (id: string) => [...(up.get(id) ?? []), ...beside(id, dagPred)]
      : (id: string) => dagPred.get(id)!;
    const after = lanes
      ? (id: string) => [...(down.get(id) ?? []), ...beside(id, dagSucc)]
      : (id: string) => dagSucc.get(id)!;
    const both = (id: string) => [...before(id), ...after(id)];
    const y = new Map<string, number>();
    for (const col of cols) {
      let at = 0;
      col.forEach((id, j) => {
        if (j > 0) at += sep(col[j - 1], id);
        y.set(id, at);
      });
    }
    // level = top to top: a node's ports start under its header, so tops in
    // line keep the trigger wires between them straight
    const settle = (col: string[], neighbours: (id: string) => string[]) => {
      const want = col.map((id) => {
        const ns = neighbours(id);
        if (!ns.length) return y.get(id)!;
        return ns.reduce((s, w) => s + y.get(w)!, 0) / ns.length;
      });
      placeInOrder(col, want, sep).forEach((v, j) => y.set(col[j], v));
    };
    for (let round = 0; round < 4; round++) {
      for (let i = 1; i < depth; i++) settle(cols[i], before);
      for (let i = depth - 2; i >= 0; i--) settle(cols[i], after);
    }
    for (let i = 0; i < depth; i++) settle(cols[i], both);

    // ── 6. onto the grid, top at 0, never closer than the gaps
    const minTop = Math.min(...ids.map((id) => y.get(id)! - (groupOf(id) ? o.groupPad.top : 0)));
    const at: Positions = {};
    const laid = new Map<string, number>();
    cols.forEach((col, i) => {
      let floor = -Infinity;
      col.forEach((id, j) => {
        if (j > 0) floor = snapUp(laid.get(col[j - 1])! + sep(col[j - 1], id), o.grid);
        const v = Math.max(snap(y.get(id)! - minTop, o.grid), floor);
        laid.set(id, v);
        if (!standIn.has(id)) at[id] = { x: colX[i], y: v };
      });
    });
    return at;
  };

  // ── 7. wires behind nodes they do not connect, cleared where it is cheap:
  //       from the plain layout and from the one with lanes, each cleared by
  //       small moves that keep it within MAX_GROWTH of the plain layout's
  //       height; the better of the two is kept (fewer wires behind nodes,
  //       then fewer crossings, then shorter wires plus height)
  const nodesOnly = column.map((col) => col.filter((id) => !standIn.has(id)));
  const plain = stack(false);
  const limit = spread(plain, byId) * (1 + MAX_GROWTH);
  let pos = plain;
  if (wires.length) {
    const a = clearWires(nodesOnly, plain, wires, byId, gapAfter, limit, o);
    const b = clearWires(nodesOnly, stack(true), wires, byId, gapAfter, limit, o);
    pos = b && (!a || better(b.key, a.key)) ? b.pos : a ? a.pos : plain;
  }
  let top = Infinity;
  let bottom = -Infinity;
  for (const id of ids) {
    const n = byId.get(id)!;
    const g = groupOf(id);
    top = Math.min(top, pos[id].y - (g ? o.groupPad.top : 0));
    bottom = Math.max(bottom, pos[id].y + tall(n) + (g ? o.groupPad.bottom : 0));
  }
  return { pos, extent: { top, bottom } };
}

/** Room kept between a wire and a node it passes. */
const WIRE_CLEARANCE = 10;
/** Half the height of the lane a wire takes through a column it skips, and
 *  the gap between two lanes side by side. */
const LANE_BAND = 6;
const LANE_GAP = 12;

/** Where a wire meets its node: the measured socket, or the header row. */
function socketAt(n: TidyNode, at: { x: number; y: number }, side: "in" | "out", port?: string | null): Point {
  const o = n.sockets?.[side]?.[port ?? ""];
  if (o) return { x: at.x + o.x, y: at.y + o.y };
  return { x: at.x + (side === "out" ? n.width : 0), y: at.y + Math.min(n.height / 2, 72) };
}

/** One wire passing behind one node: the slide of the wire (both its ends)
 *  that takes it clear above the node, and the one below. */
interface Behind { wire: TidyEdge; above: number; below: number }

/** A node with wires behind it, and the slides of the node itself that take
 *  it clear of them all (down below them, up above them). */
interface Blocked { id: string; down: number; up: number; behind: Behind[] }

/** A wire's points (TRACE_STEP apart) and the box around them. */
interface Trace { pts: Point[]; x0: number; x1: number; y0: number; y1: number; forward: boolean }

/** How far apart a wire's traced points are (px): well under the clearance
 *  kept around nodes, so no point of the drawn wire can slip between two. */
const TRACE_STEP = 6;

interface Survey {
  /** how many (wire, node) pairs: a wire behind a node it does not connect. */
  pairs: number;
  /** how many pairs of wires cross between the same two columns. */
  crossed: number;
  /** how far the wires climb or drop end to end, summed (px): level wires
   *  are the easiest to follow. */
  bend: number;
  nodes: Blocked[];
}

/**
 * Every drawn wire traced as the editor draws it, and the nodes it passes
 * behind (not its own two), with the slides (onto the grid) that clear them.
 */
function survey(
  nodes: string[],
  colOf: Map<string, number>,
  pos: Positions,
  wires: TidyEdge[],
  byId: Map<string, TidyNode>,
  grid: number,
  memo?: Map<string, Trace>,
): Survey {
  const box = (id: string) => {
    const n = byId.get(id)!;
    return { x: pos[id].x, y: pos[id].y, width: n.width, height: n.height };
  };
  // the back wires of one node pair share lanes, as TypedEdge draws them
  const lanes = new Map<TidyEdge, Lane>();
  const pairs = new Map<string, Array<{ e: TidyEdge; s: Point; t: Point }>>();
  const ends = wires.map((e) => {
    const s = socketAt(byId.get(e.source)!, pos[e.source], "out", e.sourceHandle);
    const t = socketAt(byId.get(e.target)!, pos[e.target], "in", e.targetHandle);
    if (t.x < s.x) {
      const key = `${e.source}\u0000${e.target}`;
      if (!pairs.has(key)) pairs.set(key, []);
      pairs.get(key)!.push({ e, s, t });
    }
    return { e, s, t };
  });
  for (const list of pairs.values()) {
    if (list.length < 2) continue;
    const { e } = list[0];
    const ids = list.map((w, i) => ({ id: String(i).padStart(4, "0"), sy: w.s.y, ty: w.t.y }));
    const got = pairLanes(ids, list[0].s.x, list[0].t.x, box(e.source), box(e.target));
    list.forEach((w, i) => lanes.set(w.e, got.get(ids[i].id)!));
  }
  // a wire's trace depends only on where it runs, so it is kept between
  // surveys (a move changes few wires)
  const traced = ends.map(({ e, s, t }) => {
    const lane = lanes.get(e);
    const back = t.x < s.x;
    const key = `${s.x},${s.y},${t.x},${t.y}` + (back
      ? `|${pos[e.source].y},${pos[e.target].y}|${lane ? `${lane.side},${lane.src},${lane.tgt}` : ""}`
      : "");
    let hit = memo?.get(key);
    if (!hit) {
      const pts = wireSamples(s, t, box(e.source), box(e.target), lane, TRACE_STEP);
      let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      for (const p of pts) {
        x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y);
      }
      hit = { pts, x0, x1, y0, y1, forward: !back };
      if (memo) {
        if (memo.size > 20000) memo.clear();
        memo.set(key, hit);
      }
    }
    return { e, ...hit };
  });
  const toGrid = (v: number) => Math.sign(v) * Math.ceil(Math.abs(v) / grid - 1e-9) * grid;
  const out: Blocked[] = [];
  let count = 0;
  const c = WIRE_CLEARANCE;
  for (const id of nodes) {
    const n = byId.get(id)!;
    const left = pos[id].x - c, right = pos[id].x + n.width + c;
    const top = pos[id].y - c, bottom = pos[id].y + n.height + c;
    let lo = Infinity, hi = -Infinity;
    const behind: Behind[] = [];
    for (const w of traced) {
      if (w.e.source === id || w.e.target === id) continue;
      if (w.x1 < left || w.x0 > right || w.y1 < top || w.y0 > bottom) continue;
      let wlo = Infinity, whi = -Infinity, inside = false;
      // a forward wire only ever moves right, so its points in the node's
      // span are found by bisection
      const pts = w.pts;
      let k = 0;
      if (w.forward) {
        let hiK = pts.length;
        while (k < hiK) {
          const mid = (k + hiK) >> 1;
          if (pts[mid].x < left) k = mid + 1;
          else hiK = mid;
        }
      }
      for (; k < pts.length; k++) {
        const p = pts[k];
        if (p.x > right && w.forward) break;
        if (p.x < left || p.x > right) continue;
        wlo = Math.min(wlo, p.y);
        whi = Math.max(whi, p.y);
        if (p.y > top && p.y < bottom) inside = true;
      }
      if (!inside) continue;
      count++;
      lo = Math.min(lo, wlo);
      hi = Math.max(hi, whi);
      behind.push({ wire: w.e, above: toGrid(top - whi - 1), below: toGrid(bottom - wlo + 1) });
    }
    if (behind.length) out.push({ id, down: toGrid(hi - top + 1), up: toGrid(lo - bottom - 1), behind });
  }
  // two forward wires between the same two columns cross when their ends
  // swap order
  let crossed = 0;
  for (let a = 0; a < ends.length; a++) {
    const p = ends[a];
    if (p.t.x < p.s.x) continue;
    for (let b = a + 1; b < ends.length; b++) {
      const q = ends[b];
      if (q.t.x < q.s.x || p.e.source === q.e.source || p.e.target === q.e.target) continue;
      if (colOf.get(p.e.source) !== colOf.get(q.e.source) || colOf.get(p.e.target) !== colOf.get(q.e.target)) continue;
      if ((p.s.y - q.s.y) * (p.t.y - q.t.y) < 0) crossed++;
    }
  }
  const bend = ends.reduce((sum, w) => sum + Math.abs(w.t.y - w.s.y), 0);
  return { pairs: count, crossed, bend, nodes: out };
}

/** How tall a laid out part looks: its nodes as they are drawn (the room
 *  kept below nodes that grow is not counted, it is empty until they do). */
function spread(pos: Positions, byId: Map<string, TidyNode>): number {
  let top = Infinity, bottom = -Infinity;
  for (const [id, p] of Object.entries(pos)) {
    top = Math.min(top, p.y);
    bottom = Math.max(bottom, p.y + byId.get(id)!.height);
  }
  return bottom - top;
}

/** Is score `a` better than `b`, compared term by term? */
function better(a: number[], b: number[]): boolean {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i];
  return false;
}

/** How much taller (a share of the plain layered layout's height) clearing
 *  wires may make a layout. */
const MAX_GROWTH = 0.15;

/**
 * Clear the wires that pass behind nodes with small, local moves, never
 * changing the order inside a column: for each node a wire runs behind, the
 * node slides down below the wire or up above it, or one end of the wire
 * slides so it runs over or under the node; the nodes beyond a slid one in its
 * column make room. Each step takes the move that most improves the score:
 * fewer wires behind nodes, then fewer crossings, then shorter wires (their
 * drop end to end) plus height. A move that makes the layout taller than
 * `limit` is never taken, and when nothing improves it stops: a wire left
 * behind a node is better than a scattered graph. Deterministic. Returns the
 * cleared layout and its score, or null when `start` is already too tall.
 */
function clearWires(
  column: string[][],
  start: Positions,
  wires: TidyEdge[],
  byId: Map<string, TidyNode>,
  gapAfter: (a: string, b: string) => number,
  limit: number,
  o: Opts,
): { pos: Positions; key: number[] } | null {
  if (spread(start, byId) > limit) return null;
  const colOf = new Map<string, number>();
  const rowOf = new Map<string, number>();
  column.forEach((col, i) => col.forEach((id, j) => {
    colOf.set(id, i);
    rowOf.set(id, j);
  }));
  const nodes = [...colOf.keys()];
  const memo = new Map<string, Trace>();
  // a node slides by `move`, the ones beyond it in its column only as far as
  // they must to keep their gaps
  const slide = (from: Positions, id: string, move: number): Positions => {
    const col = column[colOf.get(id)!];
    const j = rowOf.get(id)!;
    const next: Positions = { ...from, [id]: { ...from[id], y: from[id].y + move } };
    if (move > 0) {
      for (let k = j + 1; k < col.length; k++) {
        const prev = col[k - 1];
        const floor = snapUp(next[prev].y + tall(byId.get(prev)!) + gapAfter(prev, col[k]), o.grid);
        if (next[col[k]].y >= floor) break;
        next[col[k]] = { ...next[col[k]], y: floor };
      }
    } else {
      for (let k = j - 1; k >= 0; k--) {
        const below = col[k + 1];
        const ceil = Math.floor((next[below].y - gapAfter(col[k], below) - tall(byId.get(col[k])!)) / o.grid + 1e-9) * o.grid;
        if (next[col[k]].y <= ceil) break;
        next[col[k]] = { ...next[col[k]], y: ceil };
      }
    }
    return next;
  };
  const judge = (p: Positions) => {
    const s = survey(nodes, colOf, p, wires, byId, o.grid, memo);
    return { pos: p, s, key: [s.pairs, s.crossed, s.bend + spread(p, byId)] };
  };
  let cur = judge(start);
  for (let step = 0; step < 4 * nodes.length && cur.s.pairs > 0; step++) {
    let best = cur;
    for (const b of cur.s.nodes) {
      const tries: Array<[string, number]> = [[b.id, b.down], [b.id, b.up]];
      for (const x of b.behind) {
        tries.push([x.wire.source, x.above], [x.wire.source, x.below], [x.wire.target, x.above], [x.wire.target, x.below]);
      }
      for (const [id, move] of tries) {
        if (move === 0) continue;
        const p = slide(cur.pos, id, move);
        if (spread(p, byId) > limit) continue;
        const got = judge(p);
        if (better(got.key, best.key)) best = got;
      }
    }
    if (best === cur) break;
    cur = best;
  }
  return { pos: cur.pos, key: cur.key };
}

/**
 * The tops closest to `want` (least squares) that keep `col`'s order and gaps:
 * pool-adjacent-violators on the tops with each node's stacking offset taken
 * out, which turns "at least a gap below the previous" into "not above".
 */
function placeInOrder(
  col: string[],
  want: number[],
  sep: (a: string, b: string) => number,
): number[] {
  const offset: number[] = [];
  let at = 0;
  col.forEach((id, j) => {
    if (j > 0) at += sep(col[j - 1], id);
    offset.push(at);
  });
  const blocks: Array<{ sum: number; count: number }> = [];
  want.forEach((w, j) => {
    blocks.push({ sum: w - offset[j], count: 1 });
    while (blocks.length > 1) {
      const b = blocks[blocks.length - 1];
      const a = blocks[blocks.length - 2];
      if (a.sum / a.count <= b.sum / b.count) break;
      a.sum += b.sum;
      a.count += b.count;
      blocks.pop();
    }
  });
  const out: number[] = [];
  for (const b of blocks) for (let c = 0; c < b.count; c++) out.push(b.sum / b.count + offset[out.length]);
  return out;
}

/**
 * Tidy up: with two or more nodes in `selected`, only those are laid out,
 * anchored at the selection's current top-left, and every other node stays put
 * unless the tidied selection now covers it: then the covered nodes, and any
 * node past them they would land on, shift together (right, or down, whichever
 * is the shorter move) just far enough, so nothing new overlaps. With fewer, the
 * whole graph is laid out, anchored at its current top-left. Returns the new
 * position of every node that moves.
 */
export function tidyGraph(
  nodes: TidyNode[],
  edges: TidyEdge[],
  selected: string[],
  partial?: Partial<TidyOptions>,
): Positions {
  const o = options(partial);
  const chosen = new Set(selected.filter((id) => nodes.some((n) => n.id === id)));
  if (chosen.size < 2) return tidyLayout(nodes, edges, o);

  const inside = nodes.filter((n) => chosen.has(n.id));
  const pos = tidyLayout(inside, edges, o);
  const box = {
    left: Math.min(...inside.map((n) => pos[n.id].x)),
    top: Math.min(...inside.map((n) => pos[n.id].y)),
    right: Math.max(...inside.map((n) => pos[n.id].x + n.width)),
    bottom: Math.max(...inside.map((n) => pos[n.id].y + tall(n))),
  };
  const others = nodes.filter((n) => !chosen.has(n.id));
  const margin = o.rowGap;
  const covered = others.filter((n) =>
    n.x < box.right + margin && n.x + n.width > box.left - margin &&
    n.y < box.bottom + margin && n.y + n.height > box.top - margin);
  if (covered.length === 0) return pos;

  const from = {
    x: Math.min(...covered.map((n) => n.x)),
    y: Math.min(...covered.map((n) => n.y)),
  };
  const dx = snapUp(Math.max(0, box.right + margin - from.x), o.grid);
  const dy = snapUp(Math.max(0, box.bottom + margin - from.y), o.grid);
  const right = dx <= dy;
  const shift = right ? { x: dx, y: 0 } : { x: 0, y: dy };
  // the covered nodes move, and so does any node past them that a moved one
  // would now land on, all by the same amount (one move, nothing new overlaps)
  const past = others.filter((n) => (right ? n.x >= from.x : n.y >= from.y));
  const moved = new Set(covered.map((n) => n.id));
  const hits = (a: TidyNode, b: TidyNode) =>
    a.x + shift.x < b.x + b.width && a.x + shift.x + a.width > b.x &&
    a.y + shift.y < b.y + b.height && a.y + shift.y + a.height > b.y;
  for (let grew = true; grew;) {
    grew = false;
    for (const n of past) {
      if (moved.has(n.id)) continue;
      if (past.some((m) => moved.has(m.id) && hits(m, n))) {
        moved.add(n.id);
        grew = true;
      }
    }
  }
  for (const n of others) {
    if (moved.has(n.id)) pos[n.id] = { x: n.x + shift.x, y: n.y + shift.y };
  }
  return pos;
}
