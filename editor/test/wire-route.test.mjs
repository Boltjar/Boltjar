// ============================================================================
// Framework-free checks of the back-wire route (src/lib/wireRoute.ts): a wire
// whose target sits left of its source goes around its two nodes, never
// through them. Drives the REAL module, transpiled with the installed
// TypeScript compiler. Run from editor/: `node test/wire-route.test.mjs`.
//
// Guards: a forward wire is left alone (the bezier stays); a back wire leaves
// right, runs past both nodes with the margin, and enters its target from the
// left; it picks the shorter side (below or above); its upright legs step out
// of the other node's way; no segment ever crosses either node's box; and the
// drawn path rounds every corner and starts and ends on the sockets. The last
// check reads TypedEdge.tsx, where the route is drawn.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/wireRoute.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { isBackWire, backWirePoints, roundedPath, backWirePath, BACK_WIRE } =
  await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const M = BACK_WIRE.margin;
const box = (x, y, width, height) => ({ x, y, width, height });

// does the axis-aligned segment a-b pass through the inside of box b?
function crosses(a, c, b) {
  const x0 = Math.min(a.x, c.x), x1 = Math.max(a.x, c.x);
  const y0 = Math.min(a.y, c.y), y1 = Math.max(a.y, c.y);
  return x1 > b.x && x0 < b.x + b.width && y1 > b.y && y0 < b.y + b.height;
}
// every segment clears both boxes, except the short stubs on the sockets'
// own edges, which touch their node only at the socket.
function clearOfNodes(pts, sb, tb) {
  for (let i = 1; i < pts.length - 2; i++) {
    if (crosses(pts[i], pts[i + 1], sb) || crosses(pts[i], pts[i + 1], tb)) return false;
  }
  return true;
}

// ── forward wires keep their bezier ──
check("a wire to the right is not a back wire", isBackWire({ x: 0, y: 0 }, { x: 10, y: 50 }), false);
check("a wire straight down is not a back wire", isBackWire({ x: 0, y: 0 }, { x: 0, y: 50 }), false);
check("a forward wire gets no route", backWirePath({ x: 240, y: 40 }, { x: 400, y: 40 }, box(0, 0, 240, 100), box(400, 0, 240, 100)), null);
check("a back wire whose nodes are not measured yet gets no route",
  backWirePath({ x: 640, y: 40 }, { x: 0, y: 40 }, null, box(0, 0, 240, 100)), null);

// ── the For-each loop: the source (Add) right of and below its target ──
{
  const tb = box(0, 0, 240, 120);      // For-each
  const sb = box(600, 300, 240, 200);  // Add
  const s = { x: 840, y: 340 }, t = { x: 0, y: 60 };
  const pts = backWirePoints(s, t, sb, tb);
  check("a back wire has a source, four corners and a target", pts.length, 6);
  check("it starts on the source socket", pts[0], s);
  check("it ends on the target socket", pts[5], t);
  check("it leaves the source to the right by the margin", pts[1], { x: 840 + M, y: 340 });
  check("it enters the target from the left by the margin", pts[4], { x: -M, y: 60 });
  // below: (528-340)+(528-60)=656; above: (340+28)+(60+28)=456 -> above
  check("it takes the shorter side (above here)", pts[2].y, -M);
  check("the run is level", pts[2].y === pts[3].y, true);
  check("no segment crosses either node", clearOfNodes(pts, sb, tb), true);
}

// ── a source above its target runs below the lower node ──
{
  const sb = box(500, 0, 240, 100);
  const tb = box(0, 200, 240, 300);
  const s = { x: 740, y: 40 }, t = { x: 0, y: 450 };
  const pts = backWirePoints(s, t, sb, tb);
  // below: (528-40)+(528-450)=566; above: (40+28)+(450+28)=546 -> above
  check("it picks above when that is shorter", pts[2].y, -M);
  const pts2 = backWirePoints({ x: 740, y: 90 }, { x: 0, y: 480 }, sb, tb);
  // below: (528-90)+(528-480)=486; above: 118+508=626 -> below
  check("it picks below when that is shorter", pts2[2].y, 500 + M);
  check("both routes stay clear of the nodes", clearOfNodes(pts, sb, tb) && clearOfNodes(pts2, sb, tb), true);
}

// ── level nodes (the Tool's result fed from a node to its right) ──
{
  const tb = box(0, 0, 240, 340);   // Tool
  const sb = box(640, 400, 240, 180); // Time answer
  const s = { x: 880, y: 450 }, t = { x: 0, y: 60 };
  const pts = backWirePoints(s, t, sb, tb);
  check("the Tool loop clears both nodes", clearOfNodes(pts, sb, tb), true);
  check("on a tie it runs below", backWirePoints({ x: 240, y: 50 }, { x: 0, y: 50 }, box(0, 0, 240, 100), box(0, 0, 240, 100))[2].y, 100 + M);
}

// ── a self loop goes around its one node ──
{
  const b = box(0, 0, 240, 100);
  const pts = backWirePoints({ x: 240, y: 30 }, { x: 0, y: 60 }, b, b);
  check("a self loop clears its node", clearOfNodes(pts, b, b), true);
  check("a self loop leaves right and comes back left", [pts[1].x, pts[4].x], [240 + M, -M]);
}

// ── a leg steps out of the other node's way ──
{
  // the target is wider than the source and reaches past its right side, and
  // sits between the source socket and the run below
  const sb = box(300, 0, 200, 100);
  const tb = box(100, 140, 600, 100);
  const s = { x: 500, y: 90 }, t = { x: 100, y: 190 };
  const pts = backWirePoints(s, t, sb, tb);
  check("the right leg steps past the wider target", pts[1].x, 700 + M);
  check("the stepped route stays clear", clearOfNodes(pts, sb, tb), true);
  // the source reaches left past the target's left side, between the target
  // socket and the run above (taller target, so above is shorter)
  const sb2 = box(0, 0, 600, 100);
  const tb2 = box(200, 300, 200, 400);
  const pts2 = backWirePoints({ x: 600, y: 50 }, { x: 200, y: 320 }, sb2, tb2);
  check("the left leg steps past the wider source", pts2[4].x, -M);
  check("that route stays clear too", clearOfNodes(pts2, sb2, tb2), true);
}

// ── the drawn path ──
{
  const d = roundedPath([{ x: 0, y: 0 }, { x: 40, y: 0 }, { x: 40, y: 100 }, { x: -40, y: 100 }, { x: -40, y: 50 }, { x: 0, y: 50 }]);
  check("the path starts on the first point", d.startsWith("M0,0 "), true);
  check("the path ends on the last point", d.endsWith(" L0,50"), true);
  check("every corner is rounded", (d.match(/Q/g) || []).length, 4);
  check("a corner is the full radius where the segments allow", d.includes(`L${40 - BACK_WIRE.radius},0 Q40,0 40,${BACK_WIRE.radius}`), true);
  const tight = roundedPath([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 8 }, { x: 0, y: 8 }], 14);
  check("a short segment shrinks the corners to fit", tight, "M0,0 L6,0 Q10,0 10,4 L10,4 Q10,8 6,8 L0,8");
  check("a straight line has no corners", roundedPath([{ x: 0, y: 0 }, { x: 10, y: 0 }]), "M0,0 L10,0");
}

// ── TypedEdge draws it ──
{
  const edge = readFileSync(resolve(here, "../src/components/canvas/TypedEdge.tsx"), "utf8");
  check("TypedEdge routes back wires with backWirePath", /backWirePath\(/.test(edge), true);
  check("TypedEdge reads both endpoint nodes' boxes", /useInternalNode\(source\)/.test(edge) && /useInternalNode\(target\)/.test(edge), true);
  check("a forward wire keeps the bezier", /back \?\? bezier/.test(edge), true);
}

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nwire route: all checks passed");
