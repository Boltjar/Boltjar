// ============================================================================
// Framework-free harness for the Tidy up layout (src/lib/tidyLayout.ts) and the
// room it keeps below nodes that grow (src/lib/tidyRoom.ts). Drives the REAL
// modules, transpiled with the installed TypeScript compiler (their only
// imports are type-only). Run from editor/: `node test/tidy-layout.test.mjs`.
//
// Guards what the command promises: columns follow the wires left to right, no
// two nodes overlap (real sizes, very tall ones included), the gaps hold, a
// loop neither hangs nor explodes, a selection is tidied on its own and moves
// only the nodes it now covers, the same graph always gives the same layout,
// and unconnected parts keep the order they had. The last checks read App.tsx
// and Canvas.tsx, where the command is offered.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
// a module as a data: URL, its relative imports (./wireRoute) inlined the same way
function url(rel) {
  const src = readFileSync(resolve(here, "../src/lib", rel), "utf8");
  let js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  js = js.replace(/from "\.\/(\w+)"/g, (_, name) => `from "${url(`${name}.ts`)}"`);
  return "data:text/javascript," + encodeURIComponent(js);
}
const load = (rel) => import(url(rel));
const { tidyLayout, tidyGraph, TIDY_DEFAULTS } = await load("tidyLayout.ts");
const { wireSamples, pairLanes, forwardWireControls, WIRE_CURVATURE } = await load("wireRoute.ts");
const { tidyRoom, modelRoom, TIDY_ROOM } = await load("tidyRoom.ts");

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const { columnGap: COL, rowGap: ROW, grid: GRID } = TIDY_DEFAULTS;
const node = (id, x, y, width = 240, height = 120, extra = {}) => ({ id, x, y, width, height, ...extra });
const wire = (source, target) => ({ source, target });

/** Pairs of nodes whose boxes (room included) come closer than `gap`. */
function tooClose(nodes, pos, gap = 0) {
  const bad = [];
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      const pa = pos[a.id] ?? a, pb = pos[b.id] ?? b;
      const ha = a.height + (a.room ?? 0), hb = b.height + (b.room ?? 0);
      const apart = pa.x + a.width + gap <= pb.x || pb.x + b.width + gap <= pa.x
        || pa.y + ha + gap <= pb.y || pb.y + hb + gap <= pa.y;
      if (!apart) bad.push(`${a.id}/${b.id}`);
    }
  }
  return bad;
}
function crossings(pos, edges, nodes) {
  // wires between adjacent columns that cross (by node centers)
  const c = (id) => { const n = nodes.find((m) => m.id === id); return pos[id].y + n.height / 2; };
  let total = 0;
  for (let i = 0; i < edges.length; i++) {
    for (let j = i + 1; j < edges.length; j++) {
      const a = edges[i], b = edges[j];
      if (pos[a.source].x !== pos[b.source].x || pos[a.target].x !== pos[b.target].x) continue;
      const s = c(a.source) - c(b.source), t = c(a.target) - c(b.target);
      if (s * t < 0) total++;
    }
  }
  return total;
}

// ── columns follow the wires ──
// the chat shape: sources on the left, the template in the middle, the LLM and
// what reads it to the right, all typed in cramped, out of order.
const chat = [
  node("preview", 10, 10, 290, 280),
  node("llm", 40, 60, 290, 568),
  node("template", 60, 30, 374, 640),
  node("chat", 0, 0, 290, 150),
  node("clock", 20, 20, 290, 110),
  node("database", 30, 40, 290, 200),
  node("tts", 50, 50, 290, 230),
];
const chatWires = [
  wire("chat", "template"), wire("clock", "template"), wire("database", "template"),
  wire("template", "llm"), wire("llm", "preview"), wire("llm", "tts"), wire("chat", "database"),
];
const p1 = tidyLayout(chat, chatWires);
const colOf = (id) => p1[id].x;
check("a source sits left of what it feeds", colOf("chat") < colOf("database"), true);
check("the store sits left of the template it feeds", colOf("database") < colOf("template"), true);
check("the template sits left of the LLM", colOf("template") < colOf("llm"), true);
check("the LLM sits left of its preview and its TTS", [colOf("llm") < colOf("preview"), colOf("llm") < colOf("tts")], [true, true]);
check("the preview and the TTS share a column", colOf("preview"), colOf("tts"));
check("a column is its widest node plus the gap", colOf("llm") >= colOf("template") + 374 + COL, true);
check("and not much more (within one grid step)", colOf("llm") - (colOf("template") + 374 + COL) < GRID, true);
check("no two nodes overlap, and each keeps the row gap", tooClose(chat, p1, ROW - 0.001), []);
check("every position is on the grid", Object.values(p1).every((p) => p.x % GRID === 0 && p.y % GRID === 0), true);
check("anchored at the graph's top-left", [Math.min(...Object.values(p1).map((p) => p.x)), Math.min(...Object.values(p1).map((p) => p.y))], [0, 0]);

// ── gaps inside a column, with very tall nodes and room kept for growth ──
const tallOnes = [
  node("src", 0, 0),
  node("a", 0, 0, 300, 1400),
  node("b", 0, 0, 200, 60, { room: 314 }),
  node("c", 0, 0, 260, 900),
  node("sink", 0, 0),
];
const tallWires = [wire("src", "a"), wire("src", "b"), wire("src", "c"), wire("a", "sink"), wire("b", "sink"), wire("c", "sink")];
const p2 = tidyLayout(tallOnes, tallWires);
check("three siblings share a column", new Set(["a", "b", "c"].map((id) => p2[id].x)).size, 1);
check("tall nodes never overlap, room included, gap kept", tooClose(tallOnes, p2, ROW - 0.001), []);
const colB = ["a", "b", "c"].sort((x, y) => p2[x].y - p2[y].y);
const gaps = colB.slice(1).map((id, i) => {
  const prev = tallOnes.find((n) => n.id === colB[i]);
  return p2[id].y - (p2[colB[i]].y + prev.height + (prev.room ?? 0));
});
check("the gaps in a column are the row gap (to the grid step)", gaps.every((g) => g >= ROW && g < ROW + GRID), true);

// ── crossings ──
// two chains typed crossed over: a1 above b1 but a1's target below b1's.
const crossed = [node("a1", 0, 0), node("b1", 0, 400), node("a2", 400, 400), node("b2", 400, 0), node("a3", 800, 0), node("b3", 800, 400)];
const crossedWires = [wire("a1", "a2"), wire("b1", "b2"), wire("a2", "a3"), wire("b2", "b3"), wire("a1", "b3")];
const p3 = tidyLayout(crossed, crossedWires);
check("wires between columns do not cross when they need not", crossings(p3, crossedWires.filter((e) => e.source !== "a1" || e.target !== "b3"), crossed), 0);

// a chain sits level: A -> B -> C all the same height end up on one line
const line = [node("A", 0, 300), node("B", 50, 0), node("C", 100, 600)];
const p4 = tidyLayout(line, [wire("A", "B"), wire("B", "C")]);
check("a straight chain is laid out on one line", [p4.A.y, p4.B.y, p4.C.y], [0, 0, 0]);

// ── loops ──
const loop = [node("each", 0, 0), node("body", 300, 0), node("done", 600, 0), node("list", -300, 0)];
const loopWires = [wire("list", "each"), wire("each", "body"), wire("body", "each"), wire("body", "done")];
const p5 = tidyLayout(loop, loopWires);
check("a For-each loop lays out (every node placed)", Object.keys(p5).sort(), ["body", "done", "each", "list"]);
check("its loop wire back does not flip the order", [p5.list.x < p5.each.x, p5.each.x < p5.body.x, p5.body.x < p5.done.x], [true, true, true]);
const ring = Array.from({ length: 6 }, (_, i) => node(`r${i}`, i * 10, 0));
const ringWires = ring.map((n, i) => wire(n.id, `r${(i + 1) % 6}`));
const p6 = tidyLayout(ring, ringWires);
check("a ring with no source still lays out, one node per column", new Set(Object.values(p6).map((p) => p.x)).size, 6);
check("positions stay finite", Object.values(p6).every((p) => Number.isFinite(p.x) && Number.isFinite(p.y)), true);
const dense = Array.from({ length: 30 }, (_, i) => node(`d${i}`, (i * 37) % 500, (i * 91) % 700, 200 + (i % 3) * 40, 80 + (i % 5) * 60));
const denseWires = [];
for (let i = 0; i < 30; i++) for (let j = 0; j < 30; j++) if (i !== j && (i * 7 + j * 3) % 11 === 0) denseWires.push(wire(`d${i}`, `d${j}`));
const t0 = Date.now();
const p7 = tidyLayout(dense, denseWires);
check("a dense graph full of loops lays out quickly", Date.now() - t0 < 2000, true);
check("and nothing in it overlaps", tooClose(dense, p7, ROW - 0.001), []);

// ── determinism ──
check("the same graph gives the same layout", JSON.stringify(tidyLayout(chat, chatWires)), JSON.stringify(p1));
const shuffled = [...chat].reverse();
const shuffledWires = [...chatWires].reverse();
check("the order nodes and wires are listed in does not matter", JSON.stringify(Object.entries(tidyLayout(shuffled, shuffledWires)).sort()), JSON.stringify(Object.entries(p1).sort()));
check("a dense graph is deterministic too", JSON.stringify(Object.entries(tidyLayout([...dense].reverse(), [...denseWires].reverse())).sort()), JSON.stringify(Object.entries(p7).sort()));

// ── unconnected parts ──
const parts = [
  node("top1", 500, 0), node("top2", 900, 20),
  node("low1", 0, 600), node("low2", 300, 610),
  node("alone", 200, 1200),
];
const p8 = tidyLayout(parts, [wire("top1", "top2"), wire("low1", "low2")]);
check("the part that was on top stays on top", p8.top1.y < p8.low1.y && p8.low1.y < p8.alone.y, true);
check("parts are stacked, left aligned", [p8.top1.x, p8.low1.x, p8.alone.x], [0, 0, 0]);
check("parts keep the gap between them", p8.low1.y - (p8.top1.y + 120) >= TIDY_DEFAULTS.laneGap, true);
check("parts never overlap", tooClose(parts, p8, ROW - 0.001), []);
check("anchored at the scope's top-left, on the grid", [Math.min(...Object.values(p8).map((p) => p.x)), Math.min(...Object.values(p8).map((p) => p.y))], [0, 0]);

// ── groups ──
const grouped = [
  node("s", 0, 0), node("g1", 300, 0, 240, 120, { group: "G" }), node("x", 300, 150),
  node("g2", 300, 300, 240, 120, { group: "G" }), node("t", 600, 0),
];
const p9 = tidyLayout(grouped, [wire("s", "g1"), wire("s", "x"), wire("s", "g2"), wire("g1", "t"), wire("x", "t"), wire("g2", "t")]);
const middle = ["g1", "x", "g2"].sort((a, b) => p9[a].y - p9[b].y);
check("a group's members sit next to each other in a column", Math.abs(middle.indexOf("g1") - middle.indexOf("g2")), 1);
const outsider = p9.x.y > p9.g2.y ? { gap: p9.x.y - (Math.max(p9.g1.y, p9.g2.y) + 120), need: ROW + TIDY_DEFAULTS.groupPad.bottom }
  : { gap: Math.min(p9.g1.y, p9.g2.y) - (p9.x.y + 120), need: ROW + TIDY_DEFAULTS.groupPad.top };
check("a node outside the group keeps clear of the group's box", outsider.gap >= outsider.need, true);
const gp = tidyLayout([node("a", 0, 0, 240, 120, { group: "G" }), node("b", 900, 900, 240, 120, { group: "G" }), node("c", 0, 300)], []);
check("unwired members of one group stay together", Math.abs(gp.a.y - gp.b.y) < Math.abs(gp.a.y - gp.c.y) || gp.c.y > Math.max(gp.a.y, gp.b.y), true);

// ── selection only ──
const room = [
  node("s1", 0, 0), node("s2", 20, 20), node("s3", 40, 40),
  node("far", 2000, 2000),
  node("near", 300, 60),
  node("next", 560, 60),
  node("under", 0, 700),
];
const sel = tidyGraph(room, [wire("s1", "s2"), wire("s2", "s3")], ["s1", "s2", "s3"]);
check("the selection is anchored at its own top-left", [sel.s1.x, sel.s1.y], [0, 0]);
check("the tidied selection runs left to right", sel.s1.x < sel.s2.x && sel.s2.x < sel.s3.x, true);
check("a far node stays where it is", sel.far, undefined);
check("a node below the selection's reach stays where it is", sel.under, undefined);
check("a node the selection now covers is moved", sel.near !== undefined, true);
check("it moves the shorter way (down here), just clear of the selection", sel.near && sel.near.x === 300 && sel.near.y >= 120 + ROW && sel.near.y < 120 + ROW + GRID, true);
check("a node past it that it would land on moves with it, as one move", sel.next && sel.next.x === 560 && sel.next.y - 60 === sel.near.y - 60, true);
check("after the move nothing overlaps", tooClose(room, sel), []);
const sameSel = tidyGraph(room, [wire("s1", "s2"), wire("s2", "s3")], ["s1", "s2", "s3"]);
check("selection mode is deterministic", JSON.stringify(sameSel), JSON.stringify(sel));
check("one node selected tidies the whole graph", Object.keys(tidyGraph(room, [], ["s1"])).length, room.length);
check("nothing selected tidies the whole graph", Object.keys(tidyGraph(room, [], [])).length, room.length);
const stacked = [node("t1", 0, 0), node("t2", 0, 10), node("t3", 0, 20), node("below", 0, 150, 900, 100), node("wide", 0, 260, 900, 100), node("low", 0, 900, 900, 100)];
const down = tidyGraph(stacked, [wire("t1", "t2"), wire("t2", "t3")], ["t1", "t2", "t3"]);
check("a covered node below moves down when that is the shorter move", down.below && down.below.x === 0 && down.below.y > 150, true);
check("a node it would land on moves down with it", down.wide && down.wide.y - 260 === down.below.y - 150, true);
check("a node well below stays put", down.low, undefined);
check("after that move nothing overlaps", tooClose(stacked, down), []);

// ── room for nodes that grow ──
const llmDef = { id: "custom.chat", widgets: [{ name: "model", kind: "model", model_kind: "llm" }], inputs: [{ name: "prompt", growable: false }] };
const tplDef = { id: "custom.fill", widgets: [{ name: "template", kind: "code" }], inputs: [{ name: "tag", growable: true }] };
const plainDef = { id: "custom.plain", widgets: [], inputs: [{ name: "in", growable: false }] };
const num = (name, min = 0, max = 1) => ({ name, type: "float", min, max });
const manifests = [
  { id: "a/small", kind: "llm", params: [num("temperature")] },
  { id: "a/big", kind: "llm", params: [num("temperature"), num("top_p"), num("top_k"), num("num_ctx"), { name: "keep_alive", type: "text" }, { name: "think", type: "bool" }, { name: "json", type: "bool" }] },
  { id: "v/voice", kind: "tts", params: Array.from({ length: 20 }, (_, i) => num(`p${i}`)) },
];
const rows = () => 4;
check("a model node with no model keeps room for its largest model's knobs", modelRoom(manifests.filter((m) => m.kind === "llm"), () => 4, 4), 314);
check("the room is read from its own family only", tidyRoom(llmDef, {}, manifests, rows), 314);
check("a model node with a model picked keeps no room (its size is real)", tidyRoom(llmDef, { model: "a/big" }, manifests, rows), 0);
check("a model that adds port rows adds their height", modelRoom([manifests[0]], () => 6, 4) - modelRoom([manifests[0]], () => 4, 4), 2 * TIDY_ROOM.portPitch);
check("a node with a growable socket keeps a modest allowance", tidyRoom(tplDef, {}, manifests, rows), TIDY_ROOM.growRows * TIDY_ROOM.portPitch);
check("any other node keeps none", tidyRoom(plainDef, {}, manifests, rows), 0);

// ── fewer wires behind nodes, and no scattering for it ──
// The shipped examples and two graphs with fan-out and a loop, at the sizes and
// socket positions the editor measured for them (fixtures/tidy-graphs.json,
// read from the rendered editor), wired port to port. Every drawn wire is
// traced with the geometry TypedEdge draws: React Flow's bezier forward (its
// control points checked against @xyflow/system below), the route around its
// two nodes back (lib/wireRoute, in its lane beside the pair's other back wires).
// Measured against the plain layered layout (the one before wires were
// cleared, PLAIN below): wires behind nodes go down, crossings never go up,
// the height grows 15% at most, and it stays fast.
const { getBezierPath, Position } = await import("@xyflow/system");
const [rfPath] = getBezierPath({ sourceX: 10, sourceY: 20, targetX: 410, targetY: 300,
  sourcePosition: Position.Right, targetPosition: Position.Left, curvature: WIRE_CURVATURE });
const ours = forwardWireControls({ x: 10, y: 20 }, { x: 410, y: 300 });
check("the forward wire traced is React Flow's bezier", rfPath,
  `M10,20 C${ours[1].x},${ours[1].y} ${ours[2].x},${ours[2].y} 410,300`);
const edgeSrc = readFileSync(resolve(here, "../src/components/canvas/TypedEdge.tsx"), "latin1");
check("and the editor draws it with that curvature", /curvature: WIRE_CURVATURE/.test(edgeSrc), true);

/** Each drawn wire that passes through the box of a node that is not one of its ends. */
function wiresBehind(nodes, edges, pos) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const box = (id) => ({ x: pos[id].x, y: pos[id].y, width: byId.get(id).width, height: byId.get(id).height });
  const end = (id, side, port) => {
    const o = byId.get(id).sockets[side][port];
    return { x: pos[id].x + o.x, y: pos[id].y + o.y };
  };
  const drawn = edges.filter((e) => e.drawn !== false).map((e, i) => ({
    ...e, key: `w${String(i).padStart(3, "0")}`, s: end(e.source, "out", e.sourceHandle), t: end(e.target, "in", e.targetHandle) }));
  const lanes = new Map();
  for (const e of drawn) {
    if (e.t.x >= e.s.x || lanes.has(e.key)) continue;
    const pair = drawn.filter((w) => w.source === e.source && w.target === e.target && w.t.x < w.s.x);
    const got = pairLanes(pair.map((w) => ({ id: w.key, sy: w.s.y, ty: w.t.y })), e.s.x, e.t.x, box(e.source), box(e.target));
    for (const w of pair) lanes.set(w.key, got.get(w.key));
  }
  const bad = [];
  for (const e of drawn) {
    const pts = wireSamples(e.s, e.t, box(e.source), box(e.target), lanes.get(e.key), 2);
    for (const n of nodes) {
      if (n.id === e.source || n.id === e.target) continue;
      const b = box(n.id);
      if (pts.some((p) => p.x > b.x && p.x < b.x + b.width && p.y > b.y && p.y < b.y + b.height)) {
        bad.push(`${e.source}.${e.sourceHandle} -> ${e.target}.${e.targetHandle} behind ${n.id}`);
      }
    }
  }
  return bad;
}
const measured = JSON.parse(readFileSync(resolve(here, "fixtures/tidy-graphs.json"), "utf8"));
// the chat example again, every node piled up near the origin in reverse order
measured["chat, scrambled"] = {
  nodes: [...measured.chat.nodes].reverse().map((n, i) => ({ ...n, x: (i * 37) % 300, y: (i * 53) % 260 })),
  edges: measured.chat.edges,
};
/** Pairs of forward wires between the same two columns whose ends swap order. */
function wireCrossings(nodes, edges, pos) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const end = (id, side, port) => pos[id].y + byId.get(id).sockets[side][port].y;
  const ws = edges.filter((e) => e.drawn !== false && pos[e.target].x > pos[e.source].x);
  let n = 0;
  for (let a = 0; a < ws.length; a++) for (let b = a + 1; b < ws.length; b++) {
    const p = ws[a], q = ws[b];
    if (p.source === q.source || p.target === q.target) continue;
    if (pos[p.source].x !== pos[q.source].x || pos[p.target].x !== pos[q.target].x) continue;
    if ((end(p.source, "out", p.sourceHandle) - end(q.source, "out", q.sourceHandle))
      * (end(p.target, "in", p.targetHandle) - end(q.target, "in", q.targetHandle)) < 0) n++;
  }
  return n;
}
const heightOf = (nodes, pos) => Math.max(...nodes.map((n) => pos[n.id].y + n.height)) - Math.min(...nodes.map((n) => pos[n.id].y));
// the plain layered layout of each graph (main before this change): wires
// behind nodes, crossings, height; and the most wires behind nodes allowed now
const PLAIN = {
  chat: { behind: 10, crossings: 1, height: 1036, most: 5 },
  demo: { behind: 0, crossings: 0, height: 676, most: 0 },
  fanout: { behind: 1, crossings: 3, height: 952, most: 0 },
  loop: { behind: 1, crossings: 0, height: 616, most: 0 },
  "chat, scrambled": { behind: 18, crossings: 1, height: 928, most: 7 },
};
for (const [name, g] of Object.entries(measured)) {
  const t1 = performance.now();
  const laid = tidyLayout(g.nodes, g.edges);
  const ms = performance.now() - t1;
  const was = PLAIN[name];
  const behind = wiresBehind(g.nodes, g.edges, laid);
  check(`${name}: at most ${was.most} wires behind other nodes (the plain layout had ${was.behind})`,
    behind.length <= was.most || behind, true);
  check(`${name}: no more crossings than the plain layout`, wireCrossings(g.nodes, g.edges, laid) <= was.crossings, true);
  check(`${name}: at most 15% taller than the plain layout`, heightOf(g.nodes, laid) <= was.height * 1.15, true);
  check(`${name}: no two nodes overlap and the row gap holds`, tooClose(g.nodes, laid, ROW - 0.001), []);
  check(`${name}: wires still run left to right`,
    g.edges.filter((e) => e.drawn !== false && laid[e.target].x <= laid[e.source].x)
      .every((e) => e.target === "Each" || g.edges.some((f) => f.source === e.target && f.target === e.source)), true);
  check(`${name}: it lays out in under 100 ms`, ms < 100, true);
}

// ── the command is offered where it should be ──
const app = readFileSync(resolve(here, "../src/App.tsx"), "utf8");
check("the palette lists Tidy up", /id: "tidy",\s*label: "Tidy up"/.test(app), true);
check("the canvas menu offers it", /id: "tidy-canvas"/.test(app), true);
check("the selection menu offers it for 2 or more nodes", /multi \? \[\{ id: "tidy-selection"/.test(app), true);
check("its shortcut is Shift Alt T", /e\.code === "KeyT" && e\.altKey && e\.shiftKey/.test(app), true);

if (failures) {
  console.log(`\n${failures} FAILED`);
  process.exit(1);
}
console.log("\nall tidy layout checks passed");
