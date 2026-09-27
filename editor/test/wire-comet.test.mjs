// ============================================================================
// Framework-free checks of the comet a carry sends along its wire
// (src/lib/wirePulse.ts): each carry launches one, restarted per carry, up to
// five in flight so fast carries read as a stream; a comet always lands within
// COMET_MAX_MS, so a 1 s rhythm is dark between beats; with none in flight the
// wire is its normal self; reduced motion gets a plain brief tint. Drives the
// REAL module, transpiled with the installed TypeScript compiler, on a small
// stand-in DOM that records every element and animation.
// Run from editor/: `node test/wire-comet.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const read = (path) => readFileSync(resolve(here, path), "utf8");
function toDataUrl(file) {
  const js = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
  }).outputText.replace(/from\s+["'](\.{1,2}\/[^"']+)["']/g,
    (_, rel) => `from "${toDataUrl(resolve(dirname(file), `${rel}.ts`))}"`);
  return "data:text/javascript," + encodeURIComponent(js);
}
const {
  COMET_LAYERS, COMET_TAIL, COMET_MAX_MS, COMET_MIN_MS, COMET_MAX_IN_FLIGHT, TINT_MS,
  cometDuration, cometDash, CometSlots, mountComets,
} = await import(toDataUrl(resolve(here, "../src/lib/wirePulse.ts")));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- the comet's shape: a near-white head over a fading tail in the wire colour
const head = COMET_LAYERS[COMET_LAYERS.length - 1];
check("the head is the shortest, brightest, whitest layer",
  [head[0] === Math.min(...COMET_LAYERS.map((l) => l[0])), head[1], head[3] <= 20], [true, 1, true]);
check("the tail fades: opacity falls as the layers get longer",
  COMET_LAYERS.slice(1).every((l, i) => l[0] <= COMET_LAYERS[i][0] && l[1] >= COMET_LAYERS[i][1]), true);
check("the tail is the longest layer", COMET_TAIL, Math.max(...COMET_LAYERS.map((l) => l[0])));

// ---- it travels the whole wire, source port to target port
const len = 420;
const covered = (dash, offset) => [-offset, -offset + dash]; // [start, end] of the dash along the wire
for (const [dash] of COMET_LAYERS) {
  const { from, to, dasharray } = cometDash(len, dash);
  const [s0, e0] = covered(dash, from);
  const [s1] = covered(dash, to);
  check(`layer ${dash}px starts with its leading edge on the source port`, e0, 0);
  check(`layer ${dash}px starts wholly before the wire (nothing shows yet)`, s0 <= 0, true);
  check(`layer ${dash}px ends wholly past the target port`, s1 >= len, true);
  check(`layer ${dash}px is one dash: its gap outruns the trip`,
    Number(dasharray.split(" ")[1]) > len + COMET_TAIL, true);
}

// ---- timing: every comet lands within COMET_MAX_MS, so a 1 s rhythm breathes
check("a short wire still takes the minimum", cometDuration(10), COMET_MIN_MS);
check("a long wire is capped", cometDuration(5000), COMET_MAX_MS);
check("the cap leaves the wire dark before the next 1 s beat", COMET_MAX_MS <= 800, true);
check("a middling wire travels at the shared speed", Math.round(cometDuration(300)), Math.round((300 + COMET_TAIL) / 0.85));

// ---- restart and decay, pure: which slot each carry takes, how many are flying
function rhythm(every, count, ms = COMET_MAX_MS) {
  const slots = new CometSlots();
  const samples = [];
  for (let i = 0; i < count; i += 1) {
    const t = i * every;
    slots.take(t, ms);
    samples.push(slots.inFlight(t + every - 1)); // just before the next carry
  }
  return { slots, samples, end: (count - 1) * every };
}
const beat = rhythm(1000, 5);
check("1 s rhythm: dark before every next beat (breathing)", beat.samples, [0, 0, 0, 0, 0]);
check("1 s rhythm: each beat relaunches the same comet", new CometSlots().take(0, 800), 0);
const stream = rhythm(150, 40);
check("150 ms rhythm: always something in flight (a stream)", stream.samples.every((n) => n >= 1), true);
check("150 ms rhythm: never more than the cap", Math.max(...stream.samples) <= COMET_MAX_IN_FLIGHT, true);
check("at rest the wire empties: nothing in flight after the last lands",
  stream.slots.inFlight(stream.end + COMET_MAX_MS), 0);
const full = new CometSlots(3);
full.take(0, 800); full.take(10, 800); full.take(20, 800);
check("all slots flying: the next carry recycles the oldest", full.take(30, 800), 0);
check("  and the one after it the next oldest", full.take(40, 800), 1);

// ---- the DOM it drives, on a stand-in
function fakeDom() {
  const anims = [];
  const make = (tag) => {
    const e = {
      tag, attrs: {}, style: {}, children: [], parent: null, removed: false,
      setAttribute(k, v) { this.attrs[k] = String(v); },
      getAttribute(k) { return this.attrs[k] ?? null; },
      appendChild(c) { c.parent = this; this.children.push(c); return c; },
      remove() { this.removed = true; if (this.parent) this.parent.children = this.parent.children.filter((x) => x !== this); },
      getTotalLength() { return 300; },
      animate(frames, opts) {
        const a = { el: this, frames, opts, cancelled: false, onfinish: null,
          cancel() { this.cancelled = true; }, finish() { this.onfinish?.(); } };
        anims.push(a);
        return a;
      },
    };
    return e;
  };
  return { anims, make, doc: { createElementNS: (_ns, tag) => make(tag) } };
}

function setup(reduced = false) {
  const dom = fakeDom();
  const host = dom.make("g");
  const path = dom.make("path");
  path.setAttribute("d", "M0,0 C150,0 150,100 300,100");
  let t = 0;
  const comets = mountComets(host, () => ({ path, color: "var(--t-db)" }),
    { doc: dom.doc, now: () => t, reduced: () => reduced });
  return { dom, host, comets, at: (ms) => { t = ms; } };
}

{
  const { dom, host, comets, at } = setup();
  check("at rest nothing is drawn", host.children.length, 0);
  at(0); comets.fire();
  const g = host.children[0];
  check("a carry adds one comet group", host.children.length, 1);
  check("  with one path per layer", g.children.length, COMET_LAYERS.length);
  check("  flying: visible", g.attrs.visibility, "visible");
  check("  every layer follows the wire", g.children.every((p) => p.attrs.d === "M0,0 C150,0 150,100 300,100"), true);
  check("  in the wire's type colour, head nearly white",
    g.children[g.children.length - 1].style.stroke, "color-mix(in srgb, var(--t-db) 15%, white)");
  const live = dom.anims.filter((a) => !a.cancelled);
  check("  one travel per layer plus the group's fade", live.length, COMET_LAYERS.length + 1);
  check("  the head travels from the source port past the target port",
    live[COMET_LAYERS.length - 1].frames.map((f) => f.strokeDashoffset),
    [head[0], head[0] - (300 + COMET_TAIL)]);
  check("  in one shared duration", new Set(live.map((a) => a.opts.duration)).size, 1);

  // decay: the fade finishing returns the wire to its normal look
  live[live.length - 1].finish();
  check("landed: the comet hides", g.attrs.visibility, "hidden");
  check("landed: no animation left running", dom.anims.every((a) => a.cancelled), true);

  // restart: a carry after it landed reuses the same comet
  at(1000); comets.fire();
  check("the next beat reuses the comet (no new element)", host.children.length, 1);
  check("  and flies it again", g.attrs.visibility, "visible");

  // a finish from the previous flight that lands after the relaunch is stale
  const oldFade = dom.anims.filter((a) => a.el === g && a.cancelled).pop();
  oldFade.finish();
  check("a stale finish from the last flight never stops the relaunched comet",
    [g.attrs.visibility, dom.anims.filter((a) => !a.cancelled).length], ["visible", COMET_LAYERS.length + 1]);

  // a fast stream: up to the cap, then the oldest is recycled
  for (let i = 1; i <= 7; i += 1) { at(1000 + i * 100); comets.fire(); }
  check("a fast stream never draws more than the cap", host.children.length, COMET_MAX_IN_FLIGHT);
  const running = dom.anims.filter((a) => !a.cancelled && a.frames[0].opacity === 1 && a.frames.length === 3);
  check("  each comet in flight runs exactly one fade (a recycled one starts over)", running.length, COMET_MAX_IN_FLIGHT);

  comets.dispose();
  check("dispose removes every comet", host.children.length, 0);
  check("  and stops every animation", dom.anims.every((a) => a.cancelled), true);
}

{
  const { dom, host, comets } = setup(true);
  comets.fire();
  check("reduced motion: no comet, one tint path", host.children.map((c) => c.attrs.class), ["wf-edge-tint"]);
  const [first] = dom.anims;
  check("  the tint only fades, it never travels", first.frames, [{ opacity: 0.9 }, { opacity: 0 }]);
  check("  briefly", first.opts.duration, TINT_MS);
  comets.fire();
  check("  a new carry restarts the tint", [first.cancelled, dom.anims.length, host.children.length], [true, 2, 1]);
  comets.dispose();
  check("  dispose removes it", host.children.length, 0);
}

{
  const dom = fakeDom();
  const host = dom.make("g");
  const comets = mountComets(host, () => ({ path: null, color: "red" }), { doc: dom.doc, now: () => 0, reduced: () => false });
  comets.fire();
  check("no drawn path yet: a carry draws nothing", host.children.length, 0);
}

// ---- the edge and the stylesheet use it; the old dashed overlay is gone
const edge = read("../src/components/canvas/TypedEdge.tsx");
check("the edge mounts the comets into its own layer", /mountComets\(host,/.test(edge) && /className="wf-edge-comets"/.test(edge), true);
check("the edge fires a comet per carry", /onWirePulse\(id,[\s\S]*comets\.fire\(\)/.test(edge), true);
check("no React state per carry", /useState/.test(edge), false);
check("the dashed overlay is gone from the edge", /wf-edge-flow/.test(edge), false);
const cssText = read("../src/styles/editor.css");
check("the dashed overlay is gone from the stylesheet", /wf-edge-flow|@keyframes flow\b/.test(cssText), false);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall wire comet checks passed");
