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
//   • every position lands on the editor's grid.
// Sizes are the real measured ones; `room` keeps space free below a node that
// grows later. Deterministic: the same graph gives the same layout. Pure (no
// imports), so the node tests drive it.
// ============================================================================

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
}

export interface TidyEdge {
  source: string;
  target: string;
  /** how many columns the target sits right of the source: 1 (the default),
   *  or 0 for a link that may share a column (a Wireless In and its Out). */
  span?: number;
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
    return { ids, top, left, ...layoutPart(ids, byId, succ, pred, span, rank, o) };
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
  o: Opts,
): PartLayout {
  const len = (a: string, b: string) => span.get(`${a}\u0000${b}`) ?? 1;
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

  // a group's members sit next to each other where the group first appears
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
    return [...real].sort((a, b) => cmp(at(a), at(b)) || cmp(was.get(a)!, was.get(b)!));
  });

  // ── 4. x: each column as wide as its widest node
  const colX: number[] = [];
  let x = 0;
  for (const col of column) {
    colX.push(x);
    const width = Math.max(0, ...col.map((id) => byId.get(id)!.width));
    x = snapUp(x + width + o.columnGap, o.grid);
  }

  // ── 5. y: stack each column with its gaps, then sit each node level with
  //       the nodes it is wired to, keeping the order and the gaps.
  const groupOf = (id: string) => byId.get(id)!.group ?? null;
  const gapAfter = (a: string, b: string) => {
    const ga = groupOf(a);
    const gb = groupOf(b);
    if (ga === gb) return o.rowGap;
    return o.rowGap + (ga ? o.groupPad.bottom : 0) + (gb ? o.groupPad.top : 0);
  };
  const y = new Map<string, number>();
  for (const col of column) {
    let at = 0;
    col.forEach((id, j) => {
      if (j > 0) at += gapAfter(col[j - 1], id);
      y.set(id, at);
      at += tall(byId.get(id)!);
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
    placeInOrder(col, want, tall, gapAfter, byId).forEach((v, j) => y.set(col[j], v));
  };
  const before = (id: string) => dagPred.get(id)!;
  const after = (id: string) => dagSucc.get(id)!;
  const both = (id: string) => [...dagPred.get(id)!, ...dagSucc.get(id)!];
  for (let round = 0; round < 4; round++) {
    for (let i = 1; i < depth; i++) settle(column[i], before);
    for (let i = depth - 2; i >= 0; i--) settle(column[i], after);
  }
  for (let i = 0; i < depth; i++) settle(column[i], both);

  // ── 6. onto the grid, top at 0, never closer than the gaps
  const minTop = Math.min(...ids.map((id) => y.get(id)! - (groupOf(id) ? o.groupPad.top : 0)));
  const pos: Positions = {};
  column.forEach((col, i) => {
    let floor = -Infinity;
    col.forEach((id, j) => {
      if (j > 0) floor = snapUp(pos[col[j - 1]].y + tall(byId.get(col[j - 1])!) + gapAfter(col[j - 1], id), o.grid);
      const v = Math.max(snap(y.get(id)! - minTop, o.grid), floor);
      pos[id] = { x: colX[i], y: v };
    });
  });
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

/**
 * The tops closest to `want` (least squares) that keep `col`'s order and gaps:
 * pool-adjacent-violators on the tops with each node's stacking offset taken
 * out, which turns "at least a gap below the previous" into "not above".
 */
function placeInOrder(
  col: string[],
  want: number[],
  size: (n: TidyNode) => number,
  gapAfter: (a: string, b: string) => number,
  byId: Map<string, TidyNode>,
): number[] {
  const offset: number[] = [];
  let at = 0;
  col.forEach((id, j) => {
    if (j > 0) at += gapAfter(col[j - 1], id);
    offset.push(at);
    at += size(byId.get(id)!);
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
